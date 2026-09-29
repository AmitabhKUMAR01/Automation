"""LinkedIn Messaging ingest — bookmark chats + inbound job shares.

Important product rule
----------------------
Opening / scrolling a chat is **read-only ingest**. It does NOT mean you applied.
Only the existing compose → human approve → Gmail send path marks outreach done.
"""

from __future__ import annotations

import hashlib
import random
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

from playwright.sync_api import Page, sync_playwright

from outreach.config import Settings
from outreach.ingest.sources.feed_filter import (
    build_feed_needles,
    has_job_url,
    looks_like_hiring_post,
)
from outreach.ingest.sources.linkedin_feed import (
    _dismiss_overlays,
    _dump_debug,
    _launch_browser,
    _storage_path,
    _wait_until_authenticated,
)
from outreach.logging import get_logger
from outreach.protocols import RawPost

log = get_logger("ingest.linkedin_dms")

MESSAGING_URL = "https://www.linkedin.com/messaging/"

_URL_IN_TEXT = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

# Sidebar previews for shares hide the content ("Ankit sent a post"), so the
# thread must be opened to know whether it is a job.
_SHARE_PREVIEW = re.compile(
    r"\b(?:sent|shared)\s+(?:a|an)\s+(?:post|link|attachment|article|job|document)\b",
    re.IGNORECASE,
)


def _name_matches(haystack: str, needle: str) -> bool:
    h = " ".join((haystack or "").lower().split())
    n = " ".join((needle or "").lower().split())
    return bool(n) and n in h


def _synthetic_dm_url(contact: str, text: str, *, dm_kind: str) -> str:
    digest = hashlib.sha1(
        f"{dm_kind}|{contact}|{text[:600]}".encode("utf-8")
    ).hexdigest()[:20]
    return f"https://www.linkedin.com/messaging/thread/local:{digest}"


def _first_job_url(text: str) -> str | None:
    for match in _URL_IN_TEXT.finditer(text or ""):
        url = match.group(0).rstrip(").,;]}>")
        if has_job_url(url) or "linkedin.com/jobs" in url.lower():
            return url
        # LinkedIn post shares often appear in DMs too
        if re.search(r"linkedin\.com/(?:posts|feed/update)/", url, re.I):
            return url.split("?")[0]
    return None


def _wait_for_messaging(page: Page, *, max_wait_ms: int = 25_000) -> bool:
    deadline = time.time() + max_wait_ms / 1000
    while time.time() < deadline:
        ok = page.evaluate(
            """() => {
              const href = location.href.toLowerCase();
              if (href.includes('/login') || href.includes('/checkpoint') || href.includes('/authwall')) {
                return false;
              }
              return !!(
                document.querySelector('.msg-conversations-container__conversations-list')
                || document.querySelector('[data-testid*="messaging"]')
                || document.querySelector('a[href*="/messaging/thread/"]')
                || document.querySelector('.msg-conversation-listitem')
                || document.querySelector('main')
              );
            }"""
        )
        if ok and "messaging" in page.url.lower():
            return True
        page.wait_for_timeout(800)
    return "messaging" in page.url.lower()


def _scroll_conversation_list(page: Page, pixels: int = 900) -> None:
    page.evaluate(
        """(dy) => {
          const sels = [
            '.msg-conversations-container__conversations-list',
            '.msg-conversations-container',
            '[data-testid*="conversation-list"]',
            'aside',
          ];
          for (const sel of sels) {
            const el = document.querySelector(sel);
            if (el) { try { el.scrollBy(0, dy); return; } catch (_) {} }
          }
        }""",
        pixels,
    )


def _list_conversations(page: Page) -> list[dict[str, Any]]:
    """Visible conversation rows from the messaging sidebar."""
    return page.evaluate(
        """() => {
          const out = [];
          const seen = new Set();

          function push(name, preview, href, elIndex) {
            name = (name || '').trim();
            if (!name || name.length < 2) return;
            const key = name.toLowerCase() + '|' + (href || '');
            if (seen.has(key)) return;
            seen.add(key);
            out.push({
              name,
              preview: (preview || '').trim().slice(0, 280),
              href: href || null,
              elIndex: elIndex,
            });
          }

          // Classic LinkedIn messaging list
          const classic = Array.from(document.querySelectorAll(
            'a.msg-conversation-listitem__link, .msg-conversation-listitem, li.msg-conversation-listitem'
          ));
          classic.forEach((el, i) => {
            const link = el.closest('a') || el.querySelector('a') || (el.tagName === 'A' ? el : null);
            const href = link && link.href ? link.href : null;
            const nameEl = el.querySelector(
              '.msg-conversation-listitem__participant-names, .msg-conversation-card__participant-names, h3, span[class*="participant"]'
            );
            const previewEl = el.querySelector(
              '.msg-conversation-listitem__message-snippet, .msg-conversation-card__message-snippet, p'
            );
            push(
              nameEl ? nameEl.innerText : (el.innerText || '').split('\\n')[0],
              previewEl ? previewEl.innerText : '',
              href,
              i
            );
          });

          // Newer UI: any thread links in the left rail
          if (out.length === 0) {
            const links = Array.from(document.querySelectorAll('a[href*="/messaging/thread/"]'));
            links.forEach((a, i) => {
              const text = (a.innerText || '').trim();
              const lines = text.split('\\n').map(s => s.trim()).filter(Boolean);
              push(lines[0] || a.getAttribute('aria-label') || '', lines.slice(1).join(' '), a.href, i);
            });
          }

          // Fallback: listitems in aside/main that look like chats
          if (out.length === 0) {
            const items = Array.from(document.querySelectorAll('[role="listitem"], li'));
            items.forEach((el, i) => {
              const t = (el.innerText || '').trim();
              if (!t || t.length < 3 || t.length > 400) return;
              const lines = t.split('\\n').map(s => s.trim()).filter(Boolean);
              if (!lines.length) return;
              const a = el.querySelector('a[href*="messaging"]');
              push(lines[0], lines.slice(1).join(' '), a ? a.href : null, i);
            });
          }

          return out;
        }"""
    )


def _open_conversation(page: Page, conv: dict[str, Any]) -> bool:
    href = conv.get("href")
    if href and "/messaging/" in str(href):
        try:
            page.goto(href, wait_until="domcontentloaded")
            page.wait_for_timeout(1800)
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("dm_goto_failed", href=href, error=str(exc))

    # Click by visible name in the list
    name = (conv.get("name") or "").strip()
    if not name:
        return False
    try:
        # Prefer conversation list anchors containing the name
        locator = page.locator(
            "a.msg-conversation-listitem__link, a[href*='/messaging/thread/'], [role='listitem']"
        ).filter(has_text=re.compile(re.escape(name.split()[0]), re.I))
        if locator.count() == 0:
            locator = page.get_by_text(name, exact=False)
        locator.first.click(timeout=5000)
        page.wait_for_timeout(1800)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("dm_click_failed", name=name, error=str(exc))
        return False


def _extract_thread_messages(page: Page, *, limit: int) -> list[dict[str, Any]]:
    """Messages in the open thread (newest last). Read-only — no send."""
    return page.evaluate(
        """(limit) => {
          const out = [];
          const seen = new Set();

          function push(text, links, outgoing) {
            text = (text || '').replace(/\\n{3,}/g, '\\n\\n').trim();
            if (!text || text.length < 8) return;
            const key = text.slice(0, 160);
            if (seen.has(key)) return;
            seen.add(key);
            out.push({ text: text.slice(0, 6000), links: links || [], outgoing: !!outgoing });
          }

          const classic = Array.from(document.querySelectorAll(
            '.msg-s-event-listitem, .msg-s-message-list__event, li.msg-s-message-list__event'
          ));
          for (const el of classic) {
            // Shared posts render as a card outside the text body, so take
            // whichever of body / whole item carries more content.
            const body = el.querySelector('.msg-s-event-listitem__body, .msg-s-event__content, p, span');
            const bodyText = body ? (body.innerText || '') : '';
            const itemText = el.innerText || '';
            const text = itemText.length > bodyText.length ? itemText : bodyText;
            const links = Array.from(el.querySelectorAll('a[href]')).map(a => a.href);
            const outgoing = !!(
              el.classList.contains('msg-s-event-listitem--other') === false
              && (el.querySelector('.msg-s-message-group--outgoing')
                  || el.closest('.msg-s-message-group--outgoing')
                  || (el.getAttribute('data-event-urn') || '').length >= 0
                     && el.className.includes('outgoing'))
            );
            // Heuristic: LinkedIn marks outgoing groups explicitly when possible
            const group = el.closest('.msg-s-message-group');
            const isOut = group
              ? group.className.includes('outgoing')
              : el.className.includes('outgoing');
            push(text, links, isOut || outgoing);
          }

          if (out.length === 0) {
            // New UI: look for message-like blocks with links or longer text
            const candidates = Array.from(document.querySelectorAll(
              '[data-testid*="message"], [class*="message"], main p, main li'
            ));
            for (const el of candidates) {
              const text = (el.innerText || '').trim();
              if (text.length < 12 || text.length > 4000) continue;
              // Skip chrome
              if (/^(Messaging|Search|Focused|Other|Archive)/i.test(text)) continue;
              const links = Array.from(el.querySelectorAll('a[href]')).map(a => a.href);
              push(text, links, false);
            }
          }

          // Keep last N (most recent typically at bottom)
          return out.slice(-limit);
        }""",
        limit,
    )


def _classify_conversation(
    name: str,
    preview: str,
    *,
    bookmark_contacts: list[str],
    inbound_contacts: list[str],
    needles: list[str],
    scan_recent_if_no_inbound: bool,
) -> str | None:
    """Return dm_kind: bookmark | inbound, or None to skip."""
    for contact in bookmark_contacts:
        if _name_matches(name, contact):
            return "bookmark"
    if inbound_contacts:
        for contact in inbound_contacts:
            if _name_matches(name, contact):
                return "inbound"
        return None
    if not scan_recent_if_no_inbound:
        return None
    # No inbound whitelist: keep threads whose preview already looks job-related
    blob = f"{name}\n{preview}"
    if looks_like_hiring_post(blob, needles, require_role=False) or has_job_url(blob):
        return "inbound"
    if has_job_url(preview or ""):
        return "inbound"
    if _SHARE_PREVIEW.search(preview or ""):
        return "inbound"
    return None


def _message_to_raw_post(
    *,
    contact: str,
    dm_kind: str,
    text: str,
    links: list[str],
) -> RawPost | None:
    combined = text
    for link in links:
        if link and link not in combined:
            combined = f"{combined}\n{link}"
    needles = build_feed_needles([], [])
    if not looks_like_hiring_post(combined, needles, require_role=False) and not has_job_url(
        combined
    ):
        return None

    job_url = _first_job_url(combined)
    for link in links:
        if link and (has_job_url(link) or re.search(r"linkedin\.com/(?:posts|jobs|feed/update)/", link, re.I)):
            job_url = link.split("?")[0]
            break

    url = job_url or _synthetic_dm_url(contact, combined, dm_kind=dm_kind)
    if job_url and job_url.startswith("/"):
        url = urljoin("https://www.linkedin.com", job_url)

    prefix = (
        f"[LinkedIn DM {dm_kind} via {contact}]\n"
        "Note: opening this chat does not mean applied — email outreach is separate.\n\n"
    )
    return RawPost(
        url=url[:1024],
        raw_text=(prefix + combined)[:8000],
        author_name=contact,
        author_profile_url=None,
        posted_at=datetime.now(timezone.utc),
        source="linkedin_dms",
        extra={"dm_kind": dm_kind, "contact": contact},
    )


class LinkedInDmsSource:
    """Watchlist Messaging ingest. Never marks posts as applied/sent."""

    name = "linkedin_dms"

    def __init__(self, settings: Settings):
        self.settings = settings

    def fetch(self) -> list[RawPost]:
        cfg = self.settings.config.ingest
        dm = cfg.dm
        state_path = _storage_path(self.settings)
        if not state_path.exists():
            raise FileNotFoundError(
                f"LinkedIn session not found at {state_path}. "
                "Run: uv run pipeline linkedin-login"
            )

        needles = build_feed_needles(cfg.search_terms, cfg.target_roles)
        bookmark = [c.strip() for c in dm.bookmark_contacts if c.strip()]
        inbound = [c.strip() for c in dm.inbound_contacts if c.strip()]
        max_posts = cfg.max_posts_per_run
        collected: dict[str, RawPost] = {}

        with sync_playwright() as p:
            browser = _launch_browser(p, headless=cfg.linkedin_headless)
            context = browser.new_context(
                storage_state=str(state_path),
                viewport={"width": 1280, "height": 900},
                locale="en-US",
            )
            page = context.new_page()
            page.set_default_timeout(cfg.feed_navigation_timeout_ms)
            log.info(
                "linkedin_dms_open",
                headless=cfg.linkedin_headless,
                bookmark=bookmark,
                inbound=inbound or "recent_hiring_previews",
            )
            page.goto(MESSAGING_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            _dismiss_overlays(page)

            if not _wait_until_authenticated(page, max_wait_ms=15_000):
                debug_path = _dump_debug(page, self.settings)
                browser.close()
                raise RuntimeError(
                    "LinkedIn session expired or blocked "
                    f"(url={page.url}). Re-run: uv run pipeline linkedin-login "
                    f"(debug={debug_path})"
                )

            # LinkedIn often deep-links to the last thread — return to inbox list
            if "/messaging/thread/" in page.url.lower():
                page.goto(MESSAGING_URL, wait_until="domcontentloaded")
                page.wait_for_timeout(2000)

            if not _wait_for_messaging(page):
                _dump_debug(page, self.settings)
                browser.close()
                raise RuntimeError(
                    "LinkedIn Messaging UI did not load. "
                    "Check data/debug/linkedin_feed_last.png"
                )

            # Collect conversation rows (scroll list a few times)
            conv_map: dict[str, dict[str, Any]] = {}
            list_scrolls = 8
            for _ in range(list_scrolls):
                for row in _list_conversations(page):
                    key = (row.get("name") or "").strip().lower()
                    if key and key not in conv_map:
                        conv_map[key] = row
                _scroll_conversation_list(page, random.randint(700, 1100))
                page.wait_for_timeout(int(random.uniform(0.8, 1.6) * 1000))

            # Watchlist contacts may be below the scrolled list; use search box if present
            for contact in dict.fromkeys(bookmark + inbound):
                if any(_name_matches(c["name"], contact) for c in conv_map.values()):
                    continue
                found = _search_conversation(page, contact)
                if found:
                    conv_map[found["name"].strip().lower()] = found

            selected: list[tuple[str, dict[str, Any]]] = []
            for row in conv_map.values():
                kind = _classify_conversation(
                    row.get("name") or "",
                    row.get("preview") or "",
                    bookmark_contacts=bookmark,
                    inbound_contacts=inbound,
                    needles=needles,
                    scan_recent_if_no_inbound=dm.scan_recent_if_no_inbound,
                )
                if kind:
                    selected.append((kind, row))

            # Prefer bookmarks first
            selected.sort(key=lambda x: (0 if x[0] == "bookmark" else 1, x[1].get("name") or ""))
            selected = selected[: dm.max_conversations]
            log.info("linkedin_dms_selected", conversations=len(selected), listed=len(conv_map))

            for dm_kind, row in selected:
                if len(collected) >= max_posts:
                    break
                name = (row.get("name") or "").strip()
                # Return to messaging home list if needed
                if "/messaging" not in page.url.lower():
                    page.goto(MESSAGING_URL, wait_until="domcontentloaded")
                    page.wait_for_timeout(1500)

                opened = _open_conversation(page, row)
                if not opened:
                    log.warning("linkedin_dms_open_failed", contact=name)
                    continue

                # Opening the thread is intentional for scrape — NOT an apply signal
                log.info("linkedin_dms_thread_open", contact=name, dm_kind=dm_kind, applied=False)

                messages = _extract_thread_messages(page, limit=dm.max_messages_per_thread)
                kept_here = 0
                for msg in messages:
                    text = msg.get("text") or ""
                    links = list(msg.get("links") or [])
                    # Bookmark chats: prefer messages that look like job shares
                    # (both directions — you send to Abhishek, or he replies with a link)
                    post = _message_to_raw_post(
                        contact=name,
                        dm_kind=dm_kind,
                        text=text,
                        links=links,
                    )
                    if not post:
                        continue
                    if post.url in collected:
                        continue
                    collected[post.url] = post
                    kept_here += 1
                    if len(collected) >= max_posts:
                        break

                log.info(
                    "linkedin_dms_thread_done",
                    contact=name,
                    dm_kind=dm_kind,
                    messages=len(messages),
                    hiring_kept=kept_here,
                )
                page.wait_for_timeout(int(random.uniform(1.2, 2.5) * 1000))

            if not collected:
                _dump_debug(page, self.settings)

            try:
                context.storage_state(path=str(state_path))
            except Exception as exc:  # noqa: BLE001
                log.warning("linkedin_dms_state_refresh_failed", error=str(exc))

            if not cfg.linkedin_headless:
                print(
                    f"\nDM scrape finished (kept {len(collected)} job shares). "
                    "Opening chats does not mark them applied. "
                    "Closing browser in 3s...\n"
                )
                page.wait_for_timeout(3000)

            browser.close()

        posts = list(collected.values())[:max_posts]
        log.info("linkedin_dms_done", kept=len(posts))
        return posts


def _search_conversation(page: Page, contact: str) -> dict[str, Any] | None:
    """Use messaging search box to find a contact by name."""
    try:
        box = page.locator(
            'input[placeholder*="Search" i], input[aria-label*="Search" i], '
            'input[placeholder*="messaging" i]'
        ).first
        if box.count() == 0:
            return None
        box.click(timeout=3000)
        box.fill("")
        box.type(contact, delay=40)
        page.wait_for_timeout(1500)
        # Click matching result
        result = page.get_by_text(contact, exact=False).first
        result.click(timeout=4000)
        page.wait_for_timeout(1500)
        return {"name": contact, "preview": "", "href": page.url, "elIndex": -1}
    except Exception as exc:  # noqa: BLE001
        log.warning("dm_search_failed", contact=contact, error=str(exc))
        # Navigate back to messaging list
        try:
            page.goto(MESSAGING_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(1200)
        except Exception:  # noqa: BLE001
            pass
        return None
