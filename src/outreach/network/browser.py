"""Playwright helpers for connections, profile checks and sending one message."""

from __future__ import annotations

import random
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.sync_api import Page

from outreach.logging import get_logger

log = get_logger("network.browser")

CONNECTIONS_URL = "https://www.linkedin.com/mynetwork/invite-connect/connections/"

_BLOCK_URL_PARTS = ("/login", "/checkpoint", "/authwall", "/uas/")
_BLOCK_TEXT = re.compile(
    r"unusual activity|temporarily restricted|account (?:has been )?restricted|"
    r"security verification|reached the weekly|let's do a quick security check|"
    r"you've reached the limit",
    re.IGNORECASE,
)


class LinkedInBlocked(RuntimeError):
    """LinkedIn showed a login wall, captcha or restriction — stop everything."""


def check_not_blocked(page: Page) -> None:
    url = page.url.lower()
    if any(part in url for part in _BLOCK_URL_PARTS):
        raise LinkedInBlocked(f"LinkedIn redirected to {page.url}")
    try:
        body = page.evaluate("() => (document.body && document.body.innerText || '').slice(0, 20000)")
    except Exception:  # noqa: BLE001
        return
    m = _BLOCK_TEXT.search(body or "")
    if m:
        raise LinkedInBlocked(f"LinkedIn warning on page: {m.group(0)!r}")


def normalize_profile_url(href: str | None) -> str | None:
    if not href or "/in/" not in href:
        return None
    base = href.split("?")[0].split("#")[0]
    m = re.search(r"(https?://[^/]*linkedin\.com/in/[^/]+)", base)
    return (m.group(1) + "/") if m else None


def _human_pause(page: Page, lo: float = 0.8, hi: float = 2.0) -> None:
    page.wait_for_timeout(int(random.uniform(lo, hi) * 1000))


def _extract_connection_cards(page: Page) -> list[dict[str, Any]]:
    return page.evaluate(
        """() => {
          const out = [];
          const seen = new Set();
          const anchors = Array.from(document.querySelectorAll('main a[href*="/in/"]'));
          for (const a of anchors) {
            const href = (a.href || '').split('?')[0];
            if (!href || seen.has(href)) continue;
            let card = a;
            for (let i = 0; i < 8 && card; i++) {
              const t = (card.innerText || '');
              if (/connected/i.test(t) && t.length < 800) break;
              card = card.parentElement;
            }
            if (!card) continue;
            const lines = (card.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean);
            if (!lines.length) continue;
            const connected = lines.find(l => /^connected/i.test(l)) || null;
            const body = lines.filter(l => !/^(connected|message|remove|more)/i.test(l)
                                          && !/^·|^\\d+(st|nd|rd|th)$/i.test(l));
            const name = (a.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean)[0]
                         || body[0] || '';
            if (!name || name.length > 120) continue;
            const headline = body.find(l => l !== name && l.length > 2) || null;
            seen.add(href);
            out.push({ href, name, headline, connected });
          }
          return out;
        }"""
    )


def scrape_connections(page: Page, *, max_items: int) -> list[dict[str, Any]]:
    """Connections page is sorted 'Recently added' by default — newest first."""
    page.goto(CONNECTIONS_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    check_not_blocked(page)

    found: dict[str, dict[str, Any]] = {}
    idle = 0
    while len(found) < max_items and idle < 4:
        before = len(found)
        for card in _extract_connection_cards(page):
            url = normalize_profile_url(card.get("href"))
            if url and url not in found:
                found[url] = {**card, "profile_url": url}
        log.info("network_connections_scroll", found=len(found))
        clicked = False
        try:
            more = page.get_by_role("button", name=re.compile(r"show more results|load more", re.I)).first
            if more.is_visible(timeout=500):
                more.click(timeout=2000)
                clicked = True
        except Exception:  # noqa: BLE001
            pass
        if not clicked:
            page.mouse.wheel(0, random.randint(1500, 2500))
        _human_pause(page, 1.5, 3.0)
        check_not_blocked(page)
        idle = idle + 1 if len(found) == before else 0
    return list(found.values())[:max_items]


def read_experience(page: Page, profile_url: str) -> list[str]:
    """Position blocks (text) from the profile's full experience page."""
    page.goto(profile_url.rstrip("/") + "/details/experience/", wait_until="domcontentloaded")
    page.wait_for_timeout(int(random.uniform(2.0, 3.5) * 1000))
    check_not_blocked(page)
    return page.evaluate(
        """() => {
          const main = document.querySelector('main') || document.body;
          return Array.from(main.querySelectorAll('li'))
            .map(li => (li.innerText || '').trim())
            .filter(t => t.length > 10 && t.length < 3000 && /\\b(19|20)\\d{2}\\b/.test(t));
        }"""
    )


def _compose_url(page: Page) -> str | None:
    href = page.evaluate(
        """() => {
          const links = Array.from(document.querySelectorAll('a[href*="/messaging/compose"]'));
          const inMain = links.find(a => a.closest('main')) || links[0];
          return inMain ? inMain.href : null;
        }"""
    )
    if not href:
        return None
    parts = urlsplit(href)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k not in {"interop", "lipi"}]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def _message_box(page: Page):
    return page.locator(
        'div.msg-form__contenteditable[contenteditable="true"], '
        'div[role="textbox"][contenteditable="true"], '
        'div[contenteditable="true"][aria-label*="message" i]'
    ).last


def _conversation_has_history(page: Page) -> bool:
    return bool(
        page.evaluate(
            """() => {
              const sels = ['.msg-s-event-listitem', '.msg-s-message-list__event',
                            '[data-testid*="message-list"] li'];
              return sels.some(s => document.querySelectorAll(s).length > 0);
            }"""
        )
    )


def _close_chat_overlays(page: Page) -> None:
    for _ in range(3):
        try:
            btn = page.locator(
                'button.msg-overlay-bubble-header__control--close-btn, '
                'button[aria-label^="Close your conversation"], '
                'button[aria-label*="Close your draft"]'
            ).first
            if not btn.is_visible(timeout=400):
                return
            btn.click(timeout=1000)
            page.wait_for_timeout(400)
            discard = page.get_by_role("button", name=re.compile(r"^discard$", re.I)).first
            if discard.is_visible(timeout=400):
                discard.click(timeout=1000)
        except Exception:  # noqa: BLE001
            return


def send_message(page: Page, profile_url: str, message: str) -> str:
    """Returns 'sent' | 'has_history' | 'no_message_button'. Raises on block/UI failure."""
    page.goto(profile_url, wait_until="domcontentloaded")
    page.wait_for_timeout(int(random.uniform(2.5, 4.0) * 1000))
    check_not_blocked(page)
    _close_chat_overlays(page)

    # The Message button is a link to /messaging/compose/?...&interop=msgOverlay; the overlay
    # it opens is unreliable to automate, so open the same compose URL as a full page instead.
    compose_url = _compose_url(page)
    opened = False
    if compose_url:
        page.goto(compose_url, wait_until="domcontentloaded")
        opened = True
    for locator in () if opened else (
        page.locator("main").get_by_role("button", name=re.compile(r"^message\b", re.I)),
        page.locator("main").get_by_role("link", name=re.compile(r"^message\b", re.I)),
        page.locator('main a[href*="/messaging/compose"]'),
    ):
        try:
            if locator.first.is_visible(timeout=1200):
                locator.first.click(timeout=3000)
                opened = True
                break
        except Exception:  # noqa: BLE001
            continue
    if not opened:
        return "no_message_button"

    page.wait_for_timeout(int(random.uniform(3.0, 4.5) * 1000))
    check_not_blocked(page)
    box = _message_box(page)
    box.wait_for(state="visible", timeout=20_000)
    if _conversation_has_history(page):
        _close_chat_overlays(page)
        return "has_history"

    box.click()
    box.fill(message)
    # Nudge the editor so LinkedIn enables the Send button
    page.keyboard.type(" ")
    page.keyboard.press("Backspace")
    _human_pause(page, 1.0, 2.2)

    send_btn = page.locator("button.msg-form__send-button, button[type='submit']").filter(
        has_text=re.compile(r"send", re.I)
    ).last
    if send_btn.count() == 0:
        send_btn = page.get_by_role("button", name=re.compile(r"^send$", re.I)).last
    send_btn.click(timeout=5000)
    page.wait_for_timeout(2500)
    check_not_blocked(page)

    # A failed send leaves the text in the editor, so "text on page" alone proves nothing.
    try:
        leftover = (box.inner_text(timeout=2000) or "").strip()
    except Exception:  # noqa: BLE001
        leftover = ""
    delivered = not leftover and _conversation_has_history(page)
    _close_chat_overlays(page)
    if not delivered:
        raise RuntimeError("Send clicked but the message did not appear in the conversation")
    return "sent"
