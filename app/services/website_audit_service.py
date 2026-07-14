from app.utils.logger import logger
import asyncio
import uuid as _uuid
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse
import httpx
from playwright.async_api import (
    async_playwright,
    Browser,
    Page,
    ConsoleMessage,
    Error as PlaywrightError,
)
from sqlalchemy.orm import Session
from app.config.database import SessionLocal
from app.models.business_client import Business_Client
from app.models.website_audit import WebsiteAudit

MAX_CONCURRENT_PAGES  = 3       # parallel browser tabs (lower = more stable)
SETTLE_MS             = 1_500   # ms to wait after page ready (lets JS settle)
LINK_CHECK_TIMEOUT    = 6       # seconds per httpx HEAD request
MAX_LINKS_TO_CHECK    = 30      # cap links+images per site

TIMEOUT_DOMCONTENTLOADED = 15_000   # ms
TIMEOUT_COMMIT           = 10_000   # ms (fallback)

TOTAL_SITE_TIMEOUT = 45 

def _normalise_url(url: str) -> str:
    if not url:
        return url
    return url if url.startswith(("http://", "https://")) else "https://" + url


def _safe_ms(value) -> Optional[float]:
    """
    Return None if value is negative or falsy.
    Negative load_time_ms happens when wait_until='domcontentloaded' is used
    and the load event hasn't fired yet (loadEventEnd = 0, so 0 - navigationStart < 0).
    """
    if value is None:
        return None
    return float(value) if value >= 0 else None


async def _check_links(entries: list[dict]) -> list[dict]:
    """
    Fire async HEAD requests for all collected link/image URLs.
    Falls back to GET if server returns 405.
    """
    results: list[dict] = []
    if not entries:
        return results

    async with httpx.AsyncClient(
        timeout=LINK_CHECK_TIMEOUT,
        follow_redirects=True,
        verify=False,  # skip SSL verify for speed; we just want status codes
    ) as client:
        raw = await asyncio.gather(
            *[_head_request(client, e) for e in entries[:MAX_LINKS_TO_CHECK]],
            return_exceptions=True,
        )
        for entry, resp in zip(entries[:MAX_LINKS_TO_CHECK], raw):
            if isinstance(resp, Exception):
                results.append({
                    "url": entry["url"], "type": entry["type"],
                    "status_code": None, "ok": False, "error": str(resp),
                })
            else:
                results.append({
                    "url": entry["url"], "type": entry["type"],
                    "status_code": resp,
                    "ok": (resp is not None and resp < 400),
                })
    return results


async def _head_request(client: httpx.AsyncClient, entry: dict) -> Optional[int]:
    try:
        r = await client.head(entry["url"])
        if r.status_code == 405:
            r = await client.get(entry["url"])
        return r.status_code
    except Exception:
        return None

async def _audit_page(browser: Browser, business_client_id: int, url: str, db: Session) -> WebsiteAudit:
    """
    Open one tab in the shared browser, audit the URL, close the tab.
    Creates / updates the WebsiteAudit DB record.
    """
    normalised_url = _normalise_url(url)

    # Insert a 'running' record immediately so we can track progress
    audit = WebsiteAudit(
        uuid=str(_uuid.uuid4()),
        business_client_id=business_client_id,
        url=normalised_url,
        status="running",
    )
    db.add(audit)
    db.commit()
    db.refresh(audit)

    context = None
    page    = None

    try:
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            ignore_https_errors=True,
        )
        page = await context.new_page()

        # ── Console & page-error capture (register BEFORE goto) ──
        console_logs: list[dict] = []

        def _on_console(msg: ConsoleMessage):
            console_logs.append({
                "type":       msg.type,
                "text":       msg.text,
                "source_url": msg.location.get("url")        if msg.location else None,
                "line_no":    msg.location.get("lineNumber") if msg.location else None,
            })

        def _on_pageerror(err: PlaywrightError):
            console_logs.append({
                "type": "pageerror", "text": str(err),
                "source_url": None, "line_no": None,
            })

        page.on("console",   _on_console)
        page.on("pageerror", _on_pageerror)

        # ── Navigate — 3-stage fallback ─────────────────────────
        #
        # Stage 1: domcontentloaded (15 s)
        #   HTML is parsed → SEO/a11y data is available, fast for most sites.
        #
        # Stage 2: commit (10 s)  — fallback for slow/heavy sites
        #   Fires as soon as the first response bytes arrive.
        #   We then wait SETTLE_MS so JS can run before we scrape.
        #
        # Stage 3: fail — truly unreachable hosts get error_message.
        #
        load_stage = "domcontentloaded" 
        response   = None
        try:
            response = await page.goto(
                normalised_url,
                timeout=TIMEOUT_DOMCONTENTLOADED,
                wait_until="domcontentloaded",
            )
        except Exception as nav_err1:
            # Stage 2 fallback: just wait for the server to respond
            load_stage = "commit"
            logger.info(f"[AUDIT] ⚠️  {normalised_url} — domcontentloaded timeout, retrying with commit...")
            try:
                response = await page.goto(
                    normalised_url,
                    timeout=TIMEOUT_COMMIT,
                    wait_until="commit",
                )
            except Exception as nav_err2:
                # Stage 3: truly unreachable — re-raise so outer handler records failure
                raise nav_err2

        # Let JS settle before we scrape (critical for commit-stage pages)
        await page.wait_for_timeout(SETTLE_MS)

        final_url   = page.url
        http_status = response.status if response else None
        is_https    = final_url.startswith("https://")
        redirected  = final_url if final_url != normalised_url else None

        # ── Performance timing ────────────────────────────────────
        timing = await page.evaluate("""() => {
            const t = window.performance.timing;
            return {
                load_time_ms:           t.loadEventEnd         - t.navigationStart,
                dom_content_loaded_ms:  t.domContentLoadedEventEnd - t.navigationStart,
            };
        }""")

        paint_entries  = await page.evaluate("""() =>
            performance.getEntriesByType('paint')
                .map(e => ({name: e.name, startTime: e.startTime}))
        """)
        first_paint_ms = next(
            (pe["startTime"] for pe in paint_entries if pe.get("name") == "first-paint"),
            None,
        )

        # ── SEO ───────────────────────────────────────────────────
        seo = await page.evaluate("""() => {
            const getMeta = name => {
                const el = document.querySelector(
                    `meta[name='${name}'], meta[property='${name}']`
                );
                return el ? el.getAttribute('content') : null;
            };
            const allMeta = {};
            document.querySelectorAll('meta').forEach(m => {
                const k = m.getAttribute('name') || m.getAttribute('property');
                if (k) allMeta[k] = m.getAttribute('content');
            });
            return {
                title:         document.title || null,
                description:   getMeta('description'),
                keywords:      getMeta('keywords'),
                has_h1:        document.querySelectorAll('h1').length > 0,
                has_canonical: !!document.querySelector('link[rel=canonical]'),
                og_title:      getMeta('og:title'),
                og_description:getMeta('og:description'),
                og_image:      getMeta('og:image'),
                all_meta:      allMeta,
            };
        }""")

        # ── Collect link/image URLs for broken-link check ─────────
        link_entries = await page.evaluate("""() => {
            const seen = new Set();
            const out  = [];
            document.querySelectorAll('a[href]').forEach(a => {
                const h = a.href;
                if (h && !seen.has(h) &&
                    !h.startsWith('mailto:') &&
                    !h.startsWith('tel:')    &&
                    !h.startsWith('javascript:') &&
                    !h.startsWith('#')) {
                    seen.add(h);
                    out.push({url: h, type: 'link'});
                }
            });
            document.querySelectorAll('img[src]').forEach(img => {
                const s = img.src;
                if (s && !seen.has(s) && !s.startsWith('data:')) {
                    seen.add(s);
                    out.push({url: s, type: 'image'});
                }
            });
            return out;
        }""")

        # ── Accessibility ─────────────────────────────────────────
        a11y = await page.evaluate("""() => {
            const issues = [];
            let missingAlt = 0, missingLabel = 0;

            document.querySelectorAll('img').forEach(img => {
                if (!img.hasAttribute('alt') || img.getAttribute('alt') === '') {
                    missingAlt++;
                    issues.push({
                        type: 'missing_alt',
                        element: img.outerHTML.slice(0, 200),
                        detail: 'Image missing alt attribute'
                    });
                }
            });

            document.querySelectorAll(
                'input:not([type=hidden]):not([type=submit]):not([type=button])'
            ).forEach(input => {
                const id       = input.id;
                const hasLabel = id && document.querySelector(`label[for='${id}']`);
                const hasAria  = input.hasAttribute('aria-label') ||
                                 input.hasAttribute('aria-labelledby');
                if (!hasLabel && !hasAria) {
                    missingLabel++;
                    issues.push({
                        type: 'missing_label',
                        element: input.outerHTML.slice(0, 200),
                        detail: 'Input missing label'
                    });
                }
            });

            const hasLang = document.documentElement.hasAttribute('lang');
            if (!hasLang)
                issues.push({type: 'missing_lang', element: '<html>',
                              detail: 'html element missing lang attribute'});

            return {missing_alt: missingAlt, missing_label: missingLabel,
                    has_lang: hasLang, issues};
        }""")

        await page.close()
        await context.close()
        page    = None
        context = None

        # ── Broken-link HTTP checks (outside Playwright, fully async) ─
        link_results  = await _check_links(link_entries)
        broken_links  = [r for r in link_results if not r["ok"] and r["type"] == "link"]
        broken_images = [r for r in link_results if not r["ok"] and r["type"] == "image"]

        # ── Console summary ───────────────────────────────────────
        console_errors   = [c for c in console_logs if c["type"] in ("error", "pageerror")]
        console_warnings = [c for c in console_logs if c["type"] == "warning"]

        # ── Persist ───────────────────────────────────────────────
        audit.status                     = "completed"
        audit.http_status_code           = http_status
        audit.is_https                   = is_https
        audit.redirected_url             = redirected
        audit.load_time_ms               = _safe_ms(timing.get("load_time_ms"))
        audit.dom_content_loaded_ms      = timing.get("dom_content_loaded_ms")
        audit.first_paint_ms             = first_paint_ms
        audit.broken_links_count         = len(broken_links)
        audit.broken_images_count        = len(broken_images)
        audit.broken_links_detail        = broken_links + broken_images
        audit.seo_title                  = seo.get("title")
        audit.seo_description            = seo.get("description")
        audit.seo_keywords               = seo.get("keywords")
        audit.has_h1                     = seo.get("has_h1")
        audit.has_canonical              = seo.get("has_canonical")
        audit.og_title                   = seo.get("og_title")
        audit.og_description             = seo.get("og_description")
        audit.og_image                   = seo.get("og_image")
        audit.raw_seo_tags               = seo.get("all_meta")
        audit.missing_alt_images_count   = a11y.get("missing_alt", 0)
        audit.missing_label_inputs_count = a11y.get("missing_label", 0)
        audit.has_lang_attr              = a11y.get("has_lang")
        audit.accessibility_issues       = a11y.get("issues", [])
        audit.console_errors_count       = len(console_errors)
        audit.console_warnings_count     = len(console_warnings)
        audit.console_logs               = console_logs
        audit.audited_at                 = datetime.now(timezone.utc)
        audit.error_message              = None

        db.commit()
        db.refresh(audit)
        try:
            client_rec = db.query(Business_Client).filter(
                Business_Client.id == business_client_id
            ).first()
            if client_rec:
                client_rec.is_audited = True
                db.commit()
        except Exception as flag_err:
            logger.info(f"[AUDIT] ⚠️  Could not set is_audited: {flag_err}")

        logger.info(
            f"[AUDIT] ✅  {normalised_url} "
            f"(stage={load_stage}, HTTP {http_status}, "
            f"load {_safe_ms(timing.get('load_time_ms')) or 'N/A'}ms, "
            f"errors {len(console_errors)}, broken {len(broken_links)+len(broken_images)})"
        )
        return audit

    except Exception as exc:
        if page:
            try:
                await page.close()
            except Exception:
                pass
        if context:
            try:
                await context.close()
            except Exception:
                pass

        audit.status        = "failed"
        audit.error_message = str(exc)[:1000]
        audit.audited_at    = datetime.now(timezone.utc)
        try:
            db.rollback()
            db.add(audit)
            db.commit()
        except Exception as db_exc:
            logger.info(f"[AUDIT] ⚠️  Could not save failure record: {db_exc}")
        logger.info(f"[AUDIT] ❌  {normalised_url} — {exc}")
        return audit


async def audit_website(business_client_id: int, url: str, db: Session) -> WebsiteAudit:
    """Launch a one-off audit for a single URL (creates its own browser)."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            return await _audit_page(browser, business_client_id, url, db)
        finally:
            await browser.close()

async def run_audit_for_all_clients(db: Session, force: bool = False) -> dict:
    query = db.query(Business_Client).filter(
        Business_Client.url.isnot(None),
        Business_Client.url != "",
    )
    if not force:
        query = query.filter(Business_Client.is_audited == False)  # noqa: E712

    clients = query.all()

    if not clients:
        logger.info("[AUDIT] No clients with URLs found.")
        return {"total": 0, "completed": 0, "failed": 0}

    logger.info(f"[AUDIT] Starting batch for {len(clients)} clients "
          f"(concurrency={MAX_CONCURRENT_PAGES}) ...")

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_PAGES)
    completed = 0
    failed    = 0

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)

        async def _bounded(client: Business_Client):
            nonlocal completed, failed
            async with semaphore:
                task_db = SessionLocal()
                try:
                    result = await asyncio.wait_for(
                        _audit_page(browser, client.id, client.url, task_db),
                        timeout=TOTAL_SITE_TIMEOUT,
                    )
                    if result.status == "completed":
                        completed += 1
                    else:
                        failed += 1
                except asyncio.TimeoutError:
                    failed += 1
                    logger.info(f"[AUDIT] ⏱️  Hard timeout ({TOTAL_SITE_TIMEOUT}s) hit for {client.url}")
                    try:
                        stuck = (
                            task_db.query(WebsiteAudit)
                            .filter(
                                WebsiteAudit.business_client_id == client.id,
                                WebsiteAudit.status == "running",
                            )
                            .order_by(WebsiteAudit.id.desc())
                            .first()
                        )
                        if stuck:
                            stuck.status        = "failed"
                            stuck.error_message = (
                                f"Hard timeout: site did not respond within {TOTAL_SITE_TIMEOUT}s"
                            )
                            stuck.audited_at    = datetime.now(timezone.utc)
                            task_db.commit()
                    except Exception as db_err:
                        logger.info(f"[AUDIT] Could not update stuck record: {db_err}")
                finally:
                    task_db.close()

        await asyncio.gather(*[_bounded(c) for c in clients])
        await browser.close()

    summary = {"total": len(clients), "completed": completed, "failed": failed}
    logger.info(f"[AUDIT] Batch done — {summary}")
    return summary

## Run audit for all clients
def run_audit_job():
    """Sync entry point for APScheduler."""
    db = SessionLocal()
    try:
        asyncio.run(run_audit_for_all_clients(db))
    finally:
        db.close()
