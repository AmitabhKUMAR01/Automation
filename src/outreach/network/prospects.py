"""People search + connection requests (no note). Playwright helpers."""

from __future__ import annotations

import json
import random
import re
from typing import Any
from urllib.parse import urlencode

from playwright.sync_api import Page

from outreach.logging import get_logger
from outreach.network.browser import LinkedInBlocked, check_not_blocked, normalize_profile_url

log = get_logger("network.prospects")

SEARCH_URL = "https://www.linkedin.com/search/results/people/"

_WEEKLY_LIMIT = re.compile(
    r"weekly invitation limit|reached the weekly limit|you.ve reached the weekly|"
    r"invitation limit|too many pending invitations",
    re.IGNORECASE,
)
_EMAIL_REQUIRED = re.compile(r"enter (?:their|the member.s) email|knows you.*email", re.IGNORECASE)
_DEGREE = re.compile(r"\b(1st|2nd|3rd\+?)\b")


class InviteLimitReached(LinkedInBlocked):
    """LinkedIn weekly invitation limit (or email-verification wall) — stop for days."""


def search_url(keywords: str, network: list[str], page_no: int) -> str:
    params: dict[str, Any] = {
        "keywords": keywords,
        "origin": "FACETED_SEARCH",
        "network": json.dumps(network),
    }
    if page_no > 1:
        params["page"] = page_no
    return f"{SEARCH_URL}?{urlencode(params)}"


def parse_result_lines(lines: list[str], name: str) -> dict[str, Any]:
    """Pull degree / headline / location / action out of a result card's text lines."""
    clean = [ln.strip() for ln in lines if ln.strip()]
    degree = None
    headline = location = None
    idx = next((i for i, ln in enumerate(clean) if _DEGREE.search(ln)), None)
    if idx is not None:
        degree = _DEGREE.search(clean[idx]).group(1)
        rest = [
            ln
            for ln in clean[idx + 1 :]
            if not re.match(r"^(·|•|view .*profile)", ln, re.I)
            and not (_DEGREE.search(ln) and len(ln) < 30)  # "• 2nd", "2nd degree connection"
        ]
        headline = rest[0] if rest else None
        location = rest[1] if len(rest) > 1 else None
    else:
        rest = [ln for ln in clean if ln != name]
        headline = rest[0] if rest else None
        location = rest[1] if len(rest) > 1 else None
    action = None
    for ln in reversed(clean):
        if ln.lower() in {"connect", "pending", "message", "follow", "following"}:
            action = ln.lower()
            break
    return {"degree": degree, "headline": headline, "location": location, "action": action}


def _extract_results(page: Page) -> list[dict[str, Any]]:
    return page.evaluate(
        """() => {
          const out = [];
          const seen = new Set();
          for (const a of document.querySelectorAll('main a[href*="/in/"]')) {
            const href = (a.href || '').split('?')[0];
            if (!href || seen.has(href)) continue;
            const card = a.closest('li') || a.closest('[role="listitem"]')
                      || a.closest('[data-chameleon-result-urn]') || a.parentElement;
            if (!card) continue;
            const text = (card.innerText || '').trim();
            if (!text || text.length > 1500) continue;
            const name = ((a.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean)[0] || '')
                           .replace(/^View\\s+/i, '').replace(/[’']s profile$/i, '');
            if (!name || name.length > 100 || /^(status|linkedin member)/i.test(name)) continue;
            seen.add(href);
            out.push({ href, name, lines: text.split('\\n') });
          }
          return out;
        }"""
    )


def search_people(page: Page, *, keywords: str, network: list[str], max_pages: int) -> list[dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for page_no in range(1, max_pages + 1):
        page.goto(search_url(keywords, network, page_no), wait_until="domcontentloaded")
        page.wait_for_timeout(int(random.uniform(2.5, 4.5) * 1000))
        check_not_blocked(page)
        page.mouse.wheel(0, random.randint(800, 1400))
        page.wait_for_timeout(int(random.uniform(1.0, 2.0) * 1000))
        batch = _extract_results(page)
        for item in batch:
            url = normalize_profile_url(item["href"])
            if not url or url in results:
                continue
            parsed = parse_result_lines(item["lines"], item["name"])
            results[url] = {"profile_url": url, "name": item["name"], **parsed}
        log.info("prospects_search_page", keywords=keywords, page=page_no, found=len(batch))
        if not batch:
            break
        page.wait_for_timeout(int(random.uniform(3.0, 6.0) * 1000))
    return list(results.values())


def _check_invite_walls(page: Page) -> None:
    body = page.evaluate("() => (document.body && document.body.innerText || '').slice(0, 30000)")
    if _WEEKLY_LIMIT.search(body or ""):
        raise InviteLimitReached("LinkedIn weekly invitation limit reached")
    if _EMAIL_REQUIRED.search(body or ""):
        raise InviteLimitReached("LinkedIn now asks for the member's email before inviting")


def _top_card(page: Page):
    # The first section of main is the profile header; sidebars also have Connect buttons.
    return page.locator("main section").first


def send_invite(page: Page, profile_url: str) -> str:
    """Returns 'invited' | 'pending' | 'connected' | 'no_connect'. Raises on walls/limits."""
    page.goto(profile_url, wait_until="domcontentloaded")
    page.wait_for_timeout(int(random.uniform(2.5, 4.0) * 1000))
    check_not_blocked(page)
    card = _top_card(page)
    card_text = (card.inner_text(timeout=8000) or "").lower()
    if re.search(r"\bpending\b", card_text):
        return "pending"
    if re.search(r"·\s*1st\b|\b1st degree\b", card_text):
        return "connected"

    clicked = False
    direct = card.get_by_role("button", name=re.compile(r"invite .* to connect|^connect$", re.I))
    if direct.count() and direct.first.is_visible():
        direct.first.click(timeout=3000)
        clicked = True
    else:
        more = card.get_by_role("button", name=re.compile(r"^more", re.I))
        if more.count():
            more.first.click(timeout=3000)
            page.wait_for_timeout(800)
            item = page.locator(
                '[role="menu"] [aria-label*="connect" i], '
                '.artdeco-dropdown__content [aria-label*="connect" i]'
            ).first
            if item.count() and item.is_visible():
                item.click(timeout=3000)
                clicked = True
            else:
                page.keyboard.press("Escape")
    if not clicked:
        return "no_connect"

    page.wait_for_timeout(int(random.uniform(1.2, 2.2) * 1000))
    _check_invite_walls(page)
    send = page.get_by_role("button", name=re.compile(r"send without a note|^send now$|^send$", re.I))
    if send.count() == 0:
        _check_invite_walls(page)
        raise RuntimeError("Connect dialog did not show a 'Send without a note' button")
    send.first.click(timeout=4000)
    page.wait_for_timeout(int(random.uniform(1.5, 2.5) * 1000))
    _check_invite_walls(page)
    check_not_blocked(page)
    return "invited"
