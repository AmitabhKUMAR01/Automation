"""Naukri.com job discovery via Playwright — shortlist only, never auto-apply.

Product rules
-------------
- Discover + save job cards for the existing parse/match pipeline.
- Write a human-readable shortlist with Apply links.
- NEVER click Apply / Easy Apply / submit forms on Naukri.
- Polite pacing only (pauses between pages). No stealth/ban-evasion tricks.
"""

from __future__ import annotations

import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urljoin

from playwright.sync_api import Page, sync_playwright

from outreach.config import Settings
from outreach.ingest.sources.linkedin_feed import (
    _dismiss_overlays,
    _dump_debug,
    _launch_browser,
)
from outreach.logging import get_logger
from outreach.protocols import RawPost

log = get_logger("ingest.naukri")

NAUKRI_HOME = "https://www.naukri.com/"
NAUKRI_LOGIN = "https://www.naukri.com/nlogin/login"


def _slug(text: str) -> str:
    s = text.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def build_search_url(keyword: str, location: str) -> str:
    """Build a Naukri SRP URL for keyword + location."""
    k = _slug(keyword)
    loc = _slug(location)
    if loc in {"remote", "work-from-home", "wfh"}:
        # Remote-oriented search uses query string; still open keyword jobs page
        return (
            f"https://www.naukri.com/{k}-jobs"
            f"?k={quote_plus(keyword)}&l={quote_plus('remote')}"
        )
    return f"https://www.naukri.com/{k}-jobs-in-{loc}"


def _storage_path(settings: Settings) -> Path:
    return settings.resolve_path(settings.config.ingest.naukri.storage_state)


def ensure_naukri_session(settings: Settings, *, timeout_seconds: int = 300) -> Path:
    """
    Open a visible browser for manual Naukri login and save storage state.
    Optional — public search often works without login; login helps if wall appears.
    """
    path = _storage_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = _launch_browser(p, headless=False)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        page.goto(NAUKRI_LOGIN, wait_until="domcontentloaded", timeout=60_000)
        log.info("naukri_login_waiting", path=str(path), timeout_seconds=timeout_seconds)
        print(
            "\nLog into Naukri in the browser window (OTP / password as asked).\n"
            "When you land on the logged-in homepage or jobs page, return here — "
            f"waiting up to {timeout_seconds}s...\n"
            "This only saves a session for job discovery — it will NEVER auto-apply.\n"
        )
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            url = page.url.lower()
            # Logged-in surfaces leave /nlogin/
            if "naukri.com" in url and "/nlogin/" not in url and "login" not in url.split("?")[0]:
                # Prefer seeing profile/jobs chrome
                page.wait_for_timeout(2500)
                context.storage_state(path=str(path))
                browser.close()
                log.info("naukri_login_saved", path=str(path))
                return path
            # Also accept explicit mnjuser / homepage after login
            if any(x in url for x in ("/mnjuser/", "/mnj/", "homepage", "/recommendedjobs")):
                page.wait_for_timeout(2000)
                context.storage_state(path=str(path))
                browser.close()
                log.info("naukri_login_saved", path=str(path))
                return path
            page.wait_for_timeout(1500)
        browser.close()
        raise TimeoutError(
            "Naukri login timed out. Run `uv run pipeline naukri-login` again "
            "and finish login before the timer ends."
        )


def _extract_job_cards(page: Page) -> list[dict[str, Any]]:
    return page.evaluate(
        """() => {
          const out = [];
          const seen = new Set();

          function push(title, company, location, exp, salary, href, snippet) {
            title = (title || '').trim();
            href = (href || '').trim();
            if (!title || !href) return;
            if (!href.includes('naukri.com') && !href.startsWith('/')) return;
            if (href.startsWith('/')) href = 'https://www.naukri.com' + href;
            href = href.split('?')[0];
            if (seen.has(href)) return;
            seen.add(href);
            out.push({
              title,
              company: (company || '').trim(),
              location: (location || '').trim(),
              experience: (exp || '').trim(),
              salary: (salary || '').trim(),
              url: href,
              snippet: (snippet || '').trim().slice(0, 1200),
            });
          }

          // Classic / current SRP cards
          const cards = Array.from(document.querySelectorAll(
            'article.jobTuple, div.srp-jobtuple-wrapper, div.cust-job-tuple, ' +
            'div[class*="jobTuple"], article[class*="job"], div.row[type="tuple"]'
          ));
          for (const card of cards) {
            const titleA = card.querySelector(
              'a.title, a[class*="title"], a[href*="/job-listings"]'
            );
            const companyEl = card.querySelector(
              'a.comp-name, a[class*="comp"], span.comp-name, a.subTitle'
            );
            const locEl = card.querySelector(
              '.locWdth, span.loc, [class*="location"], .loc-wrap'
            );
            const expEl = card.querySelector(
              '.expwdth, span.exp, [class*="experience"], .exp-wrap'
            );
            const salEl = card.querySelector(
              '.sal, span.salary, [class*="salary"]'
            );
            const snipEl = card.querySelector(
              '.job-desc, .job-description, span.job-desc, [class*="job-desc"]'
            );
            push(
              titleA ? titleA.innerText : '',
              companyEl ? companyEl.innerText : '',
              locEl ? locEl.innerText : '',
              expEl ? expEl.innerText : '',
              salEl ? salEl.innerText : '',
              titleA ? titleA.href : '',
              snipEl ? snipEl.innerText : (card.innerText || '').slice(0, 800)
            );
          }

          // Fallback: any job-listings links
          if (out.length === 0) {
            for (const a of document.querySelectorAll('a[href*="/job-listings"]')) {
              const title = (a.innerText || a.getAttribute('title') || '').trim();
              if (title.length < 3) continue;
              const card = a.closest('article, div, li') || a.parentElement;
              push(
                title,
                '',
                '',
                '',
                '',
                a.href,
                card ? (card.innerText || '').slice(0, 800) : ''
              );
            }
          }
          return out;
        }"""
    )


def _looks_relevant(title: str, snippet: str, needles: list[str]) -> bool:
    blob = f"{title}\n{snippet}".lower()
    if not needles:
        return True
    return any(n.lower() in blob for n in needles if len(n) >= 3)


def _card_to_raw_post(card: dict[str, Any]) -> RawPost | None:
    url = (card.get("url") or "").strip()
    title = (card.get("title") or "").strip()
    if not url or not title:
        return None
    company = (card.get("company") or "").strip() or None
    location = (card.get("location") or "").strip()
    exp = (card.get("experience") or "").strip()
    salary = (card.get("salary") or "").strip()
    snippet = (card.get("snippet") or "").strip()

    parts = [
        "[Naukri discovery — MANUAL APPLY ONLY. Pipeline never auto-applies.]",
        f"Role: {title}",
    ]
    if company:
        parts.append(f"Company: {company}")
    if location:
        parts.append(f"Location: {location}")
    if exp:
        parts.append(f"Experience: {exp}")
    if salary:
        parts.append(f"Salary: {salary}")
    parts.append(f"Apply URL: {url}")
    if snippet:
        parts.append("")
        parts.append(snippet)

    return RawPost(
        url=url[:1024],
        raw_text="\n".join(parts)[:8000],
        author_name=company,
        author_profile_url=None,
        posted_at=datetime.now(timezone.utc),
        source="naukri",
        extra={
            "title": title,
            "location": location,
            "experience": exp,
            "application_method": "naukri_manual",
        },
    )


class NaukriSource:
    """Discover Naukri jobs for shortlisting. Never submits applications."""

    name = "naukri"

    def __init__(self, settings: Settings):
        self.settings = settings

    def fetch(self) -> list[RawPost]:
        cfg = self.settings.config.ingest
        nk = cfg.naukri
        state_path = _storage_path(self.settings)
        needles = list(nk.keywords) + list(cfg.target_roles)
        max_jobs = min(nk.max_jobs_per_run, cfg.max_posts_per_run)
        collected: dict[str, RawPost] = {}

        queries: list[tuple[str, str]] = []
        for kw in nk.keywords:
            for loc in nk.locations:
                queries.append((kw, loc))

        with sync_playwright() as p:
            browser = _launch_browser(p, headless=nk.headless)
            context_kwargs: dict[str, Any] = {
                "viewport": {"width": 1280, "height": 900},
                "locale": "en-IN",
            }
            if state_path.exists():
                context_kwargs["storage_state"] = str(state_path)
            context = browser.new_context(**context_kwargs)
            page = context.new_page()
            page.set_default_timeout(cfg.feed_navigation_timeout_ms)
            log.info(
                "naukri_open",
                headless=nk.headless,
                queries=len(queries),
                note="discovery_only_no_auto_apply",
            )

            for kw, loc in queries:
                if len(collected) >= max_jobs:
                    break
                url = build_search_url(kw, loc)
                log.info("naukri_search", keyword=kw, location=loc, url=url)
                try:
                    page.goto(url, wait_until="domcontentloaded")
                except Exception as exc:  # noqa: BLE001
                    log.warning("naukri_goto_failed", url=url, error=str(exc))
                    continue
                page.wait_for_timeout(2500)
                _dismiss_overlays(page)

                # Soft login-wall detect — do not try to bypass
                if "/nlogin/" in page.url.lower() or "login" in page.url.lower():
                    log.warning(
                        "naukri_login_wall",
                        hint="Run: uv run pipeline naukri-login",
                    )
                    _dump_debug(page, self.settings)
                    break

                for page_i in range(max(1, nk.max_pages_per_query)):
                    cards = _extract_job_cards(page)
                    kept = 0
                    for card in cards:
                        if not _looks_relevant(
                            card.get("title") or "",
                            card.get("snippet") or "",
                            needles,
                        ):
                            continue
                        post = _card_to_raw_post(card)
                        if not post or post.url in collected:
                            continue
                        collected[post.url] = post
                        kept += 1
                        if len(collected) >= max_jobs:
                            break

                    log.info(
                        "naukri_page",
                        keyword=kw,
                        location=loc,
                        page=page_i + 1,
                        cards=len(cards),
                        kept_total=len(collected),
                        kept_page=kept,
                    )

                    if len(collected) >= max_jobs:
                        break

                    # Next page if available (never click Apply)
                    advanced = False
                    try:
                        next_btn = page.locator(
                            'a[aria-label="Next"], a.styles_btn-secondary__2AsG_,'
                            " div.styles_pagination__3Esg6 a:has-text('Next'), "
                            "a.fright.fs14.btn-secondary.br2"
                        ).first
                        if next_btn.is_visible(timeout=800):
                            next_btn.click(timeout=3000)
                            advanced = True
                            pause = random.uniform(nk.scroll_pause_min, nk.scroll_pause_max)
                            page.wait_for_timeout(int(pause * 1000))
                    except Exception:  # noqa: BLE001
                        advanced = False
                    if not advanced:
                        # Mild scroll to load more lazy cards
                        page.evaluate("window.scrollBy(0, 1600)")
                        pause = random.uniform(nk.scroll_pause_min, nk.scroll_pause_max)
                        page.wait_for_timeout(int(pause * 1000))
                        # If still same count, stop paging this query
                        more = _extract_job_cards(page)
                        if len(more) <= len(cards):
                            break

                # Pause between keyword/location combos
                page.wait_for_timeout(int(random.uniform(2.5, 5.0) * 1000))

            if not collected:
                _dump_debug(page, self.settings)

            try:
                state_path.parent.mkdir(parents=True, exist_ok=True)
                context.storage_state(path=str(state_path))
            except Exception as exc:  # noqa: BLE001
                log.warning("naukri_state_save_failed", error=str(exc))

            if not nk.headless:
                print(
                    f"\nNaukri discovery finished (kept {len(collected)} jobs). "
                    "NO applications were submitted. "
                    "Closing browser in 3s...\n"
                )
                page.wait_for_timeout(3000)

            browser.close()

        posts = list(collected.values())[:max_jobs]
        log.info("naukri_done", kept=len(posts), auto_apply=False)
        return posts
