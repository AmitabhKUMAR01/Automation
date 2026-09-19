"""LinkedIn personal-feed ingest via Playwright + saved session."""

from __future__ import annotations

import hashlib
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from playwright.sync_api import Browser, Page, sync_playwright

from outreach.config import Settings
from outreach.ingest.sources.feed_filter import build_feed_needles, looks_like_hiring_post
from outreach.logging import get_logger
from outreach.protocols import RawPost

log = get_logger("ingest.linkedin_feed")

FEED_URL = "https://www.linkedin.com/feed/"
LOGIN_URL = "https://www.linkedin.com/login"


def _storage_path(settings: Settings) -> Path:
    return settings.resolve_path(settings.config.ingest.linkedin_storage_state)


def _launch_browser(p: Any, *, headless: bool) -> Browser:
    """Prefer installed Chrome — Google OAuth blocks Playwright's bundled Chromium."""
    launch_kwargs: dict[str, Any] = {
        "headless": headless,
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    try:
        return p.chromium.launch(channel="chrome", **launch_kwargs)
    except Exception as exc:  # noqa: BLE001
        log.warning("chrome_channel_unavailable", error=str(exc))
        return p.chromium.launch(**launch_kwargs)


def ensure_logged_in_session(settings: Settings, *, timeout_seconds: int = 300) -> Path:
    """
    Open a visible browser for manual LinkedIn login and save storage state.
    User completes login (and any 2FA) in the window.
    """
    path = _storage_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = _launch_browser(p, headless=False)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60_000)
        log.info("linkedin_login_waiting", path=str(path), timeout_seconds=timeout_seconds)
        print(
            "\nLog into LinkedIn in the browser window (including 2FA if asked).\n"
            "Prefer LinkedIn email/password over 'Sign in with Google' if possible.\n"
            "When your Home feed is visible, return here — waiting up to "
            f"{timeout_seconds}s...\n"
        )
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            url = page.url.lower()
            if "/feed" in url and "/login" not in url and "/checkpoint" not in url:
                # small settle
                page.wait_for_timeout(2000)
                context.storage_state(path=str(path))
                browser.close()
                log.info("linkedin_login_saved", path=str(path))
                return path
            page.wait_for_timeout(1500)
        browser.close()
        raise TimeoutError(
            "LinkedIn login timed out. Run `uv run pipeline linkedin-login` again "
            "and finish login before the timer ends."
        )


def _is_feed_authenticated(page: Page) -> bool:
    url = page.url.lower()
    if any(x in url for x in ("/login", "/checkpoint", "/authwall", "/uas/")):
        return False
    # Signed-in surfaces: Home feed or Messaging (incl. /messaging/thread/...)
    if "/feed" in url or "/messaging" in url:
        return True
    # Some challenges keep linkedin.com but show login chrome
    try:
        if page.locator('[data-testid="mainFeed"], [data-testid="primary-nav"]').count() > 0:
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _wait_until_authenticated(page: Page, *, max_wait_ms: int = 15_000) -> bool:
    """Allow LinkedIn redirects/challenge flash to settle before failing."""
    deadline = time.time() + max_wait_ms / 1000
    while time.time() < deadline:
        if _is_feed_authenticated(page):
            return True
        page.wait_for_timeout(800)
    return _is_feed_authenticated(page)


def _normalize_post_url(href: str | None) -> str | None:
    if not href:
        return None
    href = href.strip()
    if href.startswith("/"):
        href = urljoin("https://www.linkedin.com", href)
    if "linkedin.com" not in href:
        return None
    href = href.split("?")[0]
    if "/feed/update/" in href or "/pulse/" in href:
        return href.rstrip("/")
    # Individual share URLs only — not company "/company/x/posts/" indexes
    if re.search(r"/posts/.+-(?:activity|ugcPost)-\d+", href):
        return href.rstrip("/")
    return None


def _synthetic_url(text: str, author: str | None) -> str:
    digest = hashlib.sha1(f"{author or ''}|{text[:500]}".encode("utf-8")).hexdigest()[:16]
    return f"https://www.linkedin.com/feed/update/local:{digest}"


def _dismiss_overlays(page: Page) -> None:
    """Click common dismiss buttons so the feed is interactable."""
    for label in (
        "Accept",
        "Accept all",
        "Dismiss",
        "Not now",
        "Skip",
        "Got it",
        "No thanks",
    ):
        try:
            btn = page.get_by_role("button", name=re.compile(label, re.I)).first
            if btn.is_visible(timeout=500):
                btn.click(timeout=1000)
                page.wait_for_timeout(400)
        except Exception:  # noqa: BLE001
            continue


def _scroll_feed(page: Page, pixels: int) -> None:
    """Scroll window and LinkedIn's main column (wheel alone often does nothing)."""
    page.evaluate(
        """(dy) => {
          const candidates = [
            document.querySelector('main'),
            document.querySelector('.scaffold-finite-scroll__content'),
            document.querySelector('.scaffold-layout__main'),
            document.scrollingElement,
            document.documentElement,
            document.body,
          ].filter(Boolean);
          for (const el of candidates) {
            try { el.scrollBy(0, dy); } catch (_) {}
          }
          window.scrollBy(0, dy);
        }""",
        pixels,
    )


def _page_diagnostics(page: Page) -> dict[str, Any]:
    return page.evaluate(
        """() => {
          return {
            url: location.href,
            title: document.title,
            bodyLen: (document.body && document.body.innerText || '').length,
            hasMain: !!document.querySelector('main'),
            hasMainFeed: !!document.querySelector('[data-testid="mainFeed"]'),
            textBoxes: document.querySelectorAll('[data-testid="expandable-text-box"]').length,
            listItems: document.querySelectorAll('[data-testid="mainFeed"] [role="listitem"]').length,
            legacyFeedShared: document.querySelectorAll('.feed-shared-update-v2').length,
          };
        }"""
    )


def _dump_debug(page: Page, settings: Settings) -> Path:
    out_dir = settings.resolve_path("data/debug")
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / "linkedin_feed_last.html"
    png_path = out_dir / "linkedin_feed_last.png"
    try:
        html_path.write_text(page.content(), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        log.warning("linkedin_debug_html_failed", error=str(exc))
    try:
        page.screenshot(path=str(png_path), full_page=False)
    except Exception as exc:  # noqa: BLE001
        log.warning("linkedin_debug_screenshot_failed", error=str(exc))
    return html_path


def _expand_see_more(page: Page) -> None:
    """Expand truncated post bodies (… more)."""
    try:
        buttons = page.locator('[data-testid="expandable-text-button"]')
        count = min(buttons.count(), 25)
        for i in range(count):
            try:
                btn = buttons.nth(i)
                if btn.is_visible(timeout=200):
                    btn.click(timeout=800, force=True)
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        return


def _extract_posts_from_page(page: Page) -> list[dict[str, Any]]:
    """
    Pull visible feed cards via page JS.

    LinkedIn's 2025+ feed uses hashed CSS + data-testid hooks
    (expandable-text-box / mainFeed listitems), not classic feed-shared-* classes.
    """
    return page.evaluate(
        """() => {
          function closestListItem(el) {
            let cur = el;
            for (let i = 0; i < 20 && cur; i++) {
              if (cur.getAttribute && cur.getAttribute('role') === 'listitem') return cur;
              cur = cur.parentElement;
            }
            return el.parentElement || el;
          }

          const boxes = Array.from(
            document.querySelectorAll('[data-testid="expandable-text-box"]')
          );
          // Legacy fallback if LinkedIn rolls back UI
          if (boxes.length === 0) {
            const legacy = Array.from(document.querySelectorAll(
              'div.feed-shared-update-v2, div[data-urn*="activity"], div.occludable-update'
            ));
            return legacy.map((card) => {
              const text = (card.innerText || '').trim().slice(0, 4000);
              return { url: null, text, author: null, authorUrl: null, urn: '' };
            }).filter((x) => x.text && x.text.length >= 40);
          }

          const out = [];
          const seen = new Set();
          for (const box of boxes) {
            let text = (box.innerText || '').trim();
            text = text.replace(/\\n{3,}/g, '\\n\\n').trim();
            // Strip trailing "… more" chrome if still present
            text = text.replace(/\\.\\s*more\\s*$/i, '').trim();
            if (!text || text.length < 40) continue;

            const card = closestListItem(box);
            let url = null;
            let author = null;
            let authorUrl = null;

            for (const a of card.querySelectorAll('a[href]')) {
              const href = (a.href || '').split('?')[0];
              if (!url && (href.includes('/feed/update/') || /\\/posts\\/[^/]+/.test(href))) {
                url = href;
              }
              if (!authorUrl && (href.includes('/in/') || href.includes('/company/'))) {
                // Prefer company/person links that look like the actor, not footer chrome
                const label = (a.innerText || a.getAttribute('aria-label') || '').trim();
                if (label && label.length < 120) {
                  authorUrl = href;
                  author = label.split('\\n')[0];
                } else if (!authorUrl) {
                  authorUrl = href;
                }
              }
            }

            // Company posts often have aria-label on the logo link
            if (!author) {
              const labeled = card.querySelector('a[aria-label]');
              if (labeled) {
                author = (labeled.getAttribute('aria-label') || '')
                  .replace(/\\s*Verified\\s*$/i, '')
                  .trim() || author;
                if (!authorUrl) authorUrl = labeled.href || null;
              }
            }

            const key = (url || '') + '|' + text.slice(0, 120);
            if (seen.has(key)) continue;
            seen.add(key);
            out.push({ url, text, author, authorUrl, urn: '' });
          }
          return out;
        }"""
    )


def _wait_for_feed(page: Page, *, max_wait_ms: int = 25_000) -> bool:
    """Poll until new or legacy feed posts appear."""
    deadline = time.time() + max_wait_ms / 1000
    while time.time() < deadline:
        found = page.evaluate(
            """() => {
              return !!(
                document.querySelector('[data-testid="expandable-text-box"]')
                || document.querySelector('[data-testid="mainFeed"] [role="listitem"]')
                || document.querySelector('div.feed-shared-update-v2')
                || document.querySelector('div[data-urn*="activity"]')
              );
            }"""
        )
        if found:
            return True
        _scroll_feed(page, 400)
        page.wait_for_timeout(900)
    return False


class LinkedInFeedSource:
    name = "linkedin_feed"

    def __init__(self, settings: Settings):
        self.settings = settings

    def fetch(self) -> list[RawPost]:
        cfg = self.settings.config.ingest
        state_path = _storage_path(self.settings)
        if not state_path.exists():
            raise FileNotFoundError(
                f"LinkedIn session not found at {state_path}. "
                "Run: uv run pipeline linkedin-login"
            )

        needles = build_feed_needles(cfg.search_terms, cfg.target_roles)
        max_posts = cfg.max_posts_per_run
        max_scrolls = cfg.feed_max_scrolls

        collected: dict[str, RawPost] = {}

        with sync_playwright() as p:
            browser = _launch_browser(p, headless=cfg.linkedin_headless)
            # Do not spoof user_agent — mismatch with channel=chrome often forces re-login.
            context = browser.new_context(
                storage_state=str(state_path),
                viewport={"width": 1280, "height": 900},
                locale="en-US",
            )
            page = context.new_page()
            page.set_default_timeout(cfg.feed_navigation_timeout_ms)
            log.info("linkedin_feed_open", headless=cfg.linkedin_headless)
            page.goto(FEED_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            _dismiss_overlays(page)

            if not _wait_until_authenticated(page, max_wait_ms=15_000):
                debug_path = _dump_debug(page, self.settings)
                log.error(
                    "linkedin_session_invalid",
                    url=page.url,
                    debug_html=str(debug_path),
                )
                browser.close()
                raise RuntimeError(
                    "LinkedIn session expired or blocked "
                    f"(url={page.url}). Re-run: uv run pipeline linkedin-login"
                )

            ready = _wait_for_feed(page, max_wait_ms=25_000)
            diag = _page_diagnostics(page)
            log.info("linkedin_feed_ready", ready=ready, **diag)
            if not ready:
                debug_path = _dump_debug(page, self.settings)
                log.warning(
                    "linkedin_feed_empty_dom",
                    debug_html=str(debug_path),
                    hint="Open data/debug/linkedin_feed_last.png to see what LinkedIn showed",
                )

            for scroll_i in range(max_scrolls):
                _expand_see_more(page)
                raw_cards = _extract_posts_from_page(page)
                for card in raw_cards:
                    text = (card.get("text") or "").strip()
                    if not looks_like_hiring_post(text, needles):
                        continue
                    url = _normalize_post_url(card.get("url")) or _synthetic_url(
                        text, card.get("author")
                    )
                    if url in collected:
                        continue
                    collected[url] = RawPost(
                        url=url,
                        raw_text=text[:8000],
                        author_name=(card.get("author") or None),
                        author_profile_url=(card.get("authorUrl") or None),
                        posted_at=datetime.now(timezone.utc),
                        source="linkedin_feed",
                        extra={"urn": card.get("urn")},
                    )
                    if len(collected) >= max_posts:
                        break

                log.info(
                    "linkedin_feed_scroll",
                    scroll=scroll_i + 1,
                    cards=len(raw_cards),
                    hiring_kept=len(collected),
                )
                if len(collected) >= max_posts:
                    break

                _scroll_feed(page, random.randint(1600, 2600))
                pause = random.uniform(cfg.feed_scroll_pause_min, cfg.feed_scroll_pause_max)
                page.wait_for_timeout(int(pause * 1000))

            if not collected:
                _dump_debug(page, self.settings)

            # Refresh storage state (session cookies may rotate)
            try:
                context.storage_state(path=str(state_path))
            except Exception as exc:  # noqa: BLE001
                log.warning("linkedin_state_refresh_failed", error=str(exc))

            if not cfg.linkedin_headless:
                print(
                    f"\nFeed scrape finished (kept {len(collected)} hiring posts). "
                    "Closing browser in 3s...\n"
                )
                page.wait_for_timeout(3000)

            browser.close()

        posts = list(collected.values())[:max_posts]
        log.info("linkedin_feed_done", kept=len(posts))
        return posts
