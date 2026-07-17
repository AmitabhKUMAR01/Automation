"""
linkedin_search_connector.py

Playwright automation for the new LinkedIn search-based connection flow.

LinkedIn now uses fully obfuscated CSS class names (e.g. _467e126b) that
change with every deploy. We therefore rely exclusively on:
  - aria-label attributes  (stable across deploys)
  - href patterns           (stable)
  - Role-based selectors    (stable)

Phase 1 — Keyword Search (per position from LinkedinSearchConfig):
  - Navigate to LinkedIn people search: "{position} {location}"
  - Wait for "Invite … to connect" buttons to appear (aria-label)
  - Send up to SEARCH_LIMIT connections without a note
  - Save each sent connection to linkedin_search_contacts table

Phase 2 — My Network "People you may know":
  - Navigate to linkedin.com/mynetwork/grow/
  - Click "Show all" (href contains mynetwork/grow)
  - Find all "Invite … to connect" buttons on the page
  - Send up to NETWORK_LIMIT connections without a note
"""

import json
import re
import uuid as _uuid
from datetime import datetime, timezone
from urllib.parse import quote

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from sqlalchemy.orm import Session
import os

from app.config.database import SessionLocal
from app.models.linkedin_search_contact import LinkedinSearchContact
from app.models.profile_setting import ProfileSetting
from app.scraper.linkedin_finder import (
    HEADLESS,
    LinkedInSessionExpiredError,
    _rand_delay,
    _check_session,
)
from app.scraper.browser_utils import pick_fingerprint, new_stealth_page
from app.utils.logger import logger


# ── Custom exceptions ──────────────────────────────────────────────────────────

class LinkedInWeeklyLimitError(Exception):
    """Raised when LinkedIn blocks the connection due to the weekly invite limit."""
    pass

# ── Constants ──────────────────────────────────────────────────────────────────
# Hard ceilings enforced in code — env var cannot exceed these
_SEARCH_CONNECT_HARD_MAX  = 10
_NETWORK_CONNECT_HARD_MAX = 8

SEARCH_CONNECT_LIMIT = min(
    int(os.getenv("SEARCH_CONNECT_LIMIT",  "8")),
    _SEARCH_CONNECT_HARD_MAX,
)
NETWORK_CONNECT_LIMIT = min(
    int(os.getenv("NETWORK_CONNECT_LIMIT", "5")),
    _NETWORK_CONNECT_HARD_MAX,
)

# How long to wait (ms) for the first Invite button to appear after navigation
RESULTS_WAIT_TIMEOUT = 15000


# ── Helpers ────────────────────────────────────────────────────────────────────

def _normalise_slug(profile_url: str) -> str:
    """Extract the /in/<slug> part for deduplication."""
    if "/in/" in profile_url:
        return profile_url.split("/in/")[1].split("?")[0].rstrip("/").lower()
    return profile_url.lower()


def _already_sent(db: Session, profile_url: str) -> bool:
    """Return True if we've already sent a connection to this profile URL."""
    slug = _normalise_slug(profile_url)
    return (
        db.query(LinkedinSearchContact)
        .filter(LinkedinSearchContact.profile_url.contains(slug))
        .first()
    ) is not None


def _save_contact(
    db: Session,
    name: str,
    profile_url: str,
    position: str,
    location: str,
    source: str,
    profile_id: int,
) -> None:
    """Persist a sent connection to linkedin_search_contacts."""
    try:
        record = LinkedinSearchContact(
            uuid               = str(_uuid.uuid4()),
            name               = name,
            profile_url        = profile_url,
            position           = position,
            location           = location,
            source             = source,
            connection_sent    = True,
            connection_sent_at = datetime.now(timezone.utc),
            profile_id         = profile_id,
        )
        db.add(record)
        db.commit()
    except Exception as exc:
        logger.warning(f"[SEARCH CONNECTOR] Could not save contact: {exc}")
        db.rollback()


def _get_connect_buttons(page):
    """
    Return all visible Connect/Invite buttons on the current page.
    Covers two LinkedIn UI patterns:
      - Search results: button or <a> with visible text 'Connect'
      - My Network:     button with aria-label 'Invite {name} to connect'
    """
    return page.locator(
        "button[aria-label*='Invite'][aria-label*='connect'], "
        "button[aria-label*='Connect'], "
        "button:has-text('Connect'), "
        "a:has-text('Connect')"
    )


def _extract_name_from_aria(aria_label: str) -> str:
    """
    Extract name from aria-label like "Invite Ahmed Al Hamad to connect".
    Returns empty string if not that pattern.
    """
    match = re.search(r"Invite (.+?) to connect", aria_label, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return ""  # plain 'Connect' button — name extracted separately


def _find_profile_url_near_button(page, btn) -> str:
    """
    Find the LinkedIn profile /in/ URL closest to a given Connect button.
    Walks up the DOM tree to find the nearest ancestor that contains an
    <a href="/in/..."> link.
    """
    try:
        href = btn.evaluate("""
            el => {
                let node = el;
                for (let i = 0; i < 12; i++) {
                    if (!node || !node.parentElement) break;
                    node = node.parentElement;
                    const links = node.querySelectorAll('a[href*="/in/"]');
                    for (const link of links) {
                        const h = link.href || '';
                        if (h.includes('/in/') && !h.includes('/preload/')) return h;
                    }
                }
                return '';
            }
        """)
        if href and "/in/" in href:
            return href.split("?")[0]
    except Exception:
        pass
    return ""

def _extract_name_from_nearby_dom(page, btn) -> str:
    """
    For search result cards that use plain 'Connect' text (no name in aria-label),
    walk up the DOM to find the person's name from a nearby profile link or span.
    """
    try:
        name = btn.evaluate("""
            el => {
                let node = el;
                for (let i = 0; i < 8; i++) {
                    if (!node || !node.parentElement) break;
                    node = node.parentElement;
                    // Profile link text is usually the person's name
                    const links = node.querySelectorAll('a[href*="/in/"]');
                    for (const link of links) {
                        const txt = (link.innerText || '').trim().split('\\n')[0];
                        if (txt && txt.length > 1) return txt;
                    }
                    // Fallback: aria-hidden spans are often used for names
                    const spans = node.querySelectorAll("span[aria-hidden='true']");
                    for (const s of spans) {
                        const txt = (s.innerText || '').trim();
                        if (txt && txt.length > 2 && !txt.includes('Connect')) return txt;
                    }
                }
                return '';
            }
        """)
        return (name or "").strip()
    except Exception:
        return ""



def _check_weekly_limit_error(page) -> bool:
    """
    Check if LinkedIn has shown a 'weekly limit reached' error
    in a toast notification or modal. Returns True if the limit is hit.
    """
    error_phrases = [
        "weekly limit",
        "invitation was not sent",
        "invitation to",
        "try again next week",
        "reached the limit",
    ]
    try:
        # Check toast notifications (the most common way LinkedIn shows this)
        toast = page.locator(
            "[role='alert'], "
            "div.artdeco-toast-item, "
            "div[data-test-artdeco-toast-item]"
        ).all()
        for t in toast:
            txt = (t.inner_text() or "").lower()
            if any(phrase in txt for phrase in error_phrases):
                logger.warning(f"[SEARCH CONNECTOR] ⛔ Weekly limit toast detected: {txt[:120]}")
                return True

        # Also check if a dialog contains the error
        dialog = page.locator("div[role='dialog']").first
        if dialog.count() > 0:
            txt = (dialog.inner_text() or "").lower()
            if any(phrase in txt for phrase in error_phrases):
                logger.warning(f"[SEARCH CONNECTOR] ⛔ Weekly limit dialog detected: {txt[:120]}")
                page.keyboard.press("Escape")
                return True

    except Exception:
        pass
    return False


def _send_connection_from_button(page, btn) -> bool:
    """
    Click a Connect/Invite button and dismiss the 'Add a note' modal
    by clicking 'Send without a note'.
    Returns True on success.
    Raises LinkedInWeeklyLimitError if LinkedIn blocks due to weekly limit.
    """
    try:
        btn.scroll_into_view_if_needed()
        btn.click(force=True)
        _rand_delay(1, 1.5)

        # Check immediately for weekly limit error (sometimes shown before modal)
        if _check_weekly_limit_error(page):
            raise LinkedInWeeklyLimitError("Weekly invitation limit reached")

        # Wait briefly for modal to appear
        try:
            page.wait_for_selector(
                "div[role='dialog'], button[aria-label='Send without a note'], "
                "button:has-text('Send without a note')",
                timeout=3000,
            )
        except PlaywrightTimeout:
            # No modal — check for limit error before assuming success
            if _check_weekly_limit_error(page):
                raise LinkedInWeeklyLimitError("Weekly invitation limit reached")
            # No modal + no error = directly sent
            return True

        # Click 'Send without a note'
        send_btn = page.locator(
            "button[aria-label='Send without a note'], "
            "button:has-text('Send without a note')"
        ).first
        if send_btn.count() > 0 and send_btn.is_visible(timeout=2000):
            send_btn.click(force=True)
            _rand_delay(1.5, 2)

            # After clicking Send, check for weekly limit error
            if _check_weekly_limit_error(page):
                raise LinkedInWeeklyLimitError("Weekly invitation limit reached")
            return True

        # Fallback: click the generic 'Send' button inside the dialog
        dialog = page.locator("div[role='dialog']").first
        if dialog.count() > 0:
            send_fallback = dialog.locator("button:has-text('Send')").first
            if send_fallback.count() > 0:
                send_fallback.click(force=True)
                _rand_delay(1.5, 2)

                # Check again after fallback send
                if _check_weekly_limit_error(page):
                    raise LinkedInWeeklyLimitError("Weekly invitation limit reached")
                return True

        # Give up — dismiss modal
        page.keyboard.press("Escape")
        return False

    except LinkedInWeeklyLimitError:
        raise  # propagate up — stops the whole job
    except PlaywrightTimeout:
        return False
    except Exception as exc:
        logger.info(f"[SEARCH CONNECTOR] Error sending connection: {exc}")
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        return False


def _wait_for_results(page, timeout: int = RESULTS_WAIT_TIMEOUT) -> bool:
    """
    Wait until at least one 'Invite ... to connect' button is visible.
    Returns True if found, False if timeout.
    """
    try:
        page.wait_for_selector(
            "button[aria-label*='Invite'][aria-label*='connect'], "
            "button[aria-label*='Connect']",
            timeout=timeout,
        )
        return True
    except PlaywrightTimeout:
        return False


# ── Phase 1: Search-based connections ─────────────────────────────────────────

def _run_search_phase(page, db: Session, positions: list, location: str, profile_id: int) -> int:
    """
    For each position: navigate to LinkedIn people search, wait for results,
    and send up to SEARCH_CONNECT_LIMIT connections total.
    """
    total_sent = 0

    for position in positions:
        if total_sent >= SEARCH_CONNECT_LIMIT:
            logger.info(f"[SEARCH CONNECTOR] Reached search limit ({SEARCH_CONNECT_LIMIT}) — stopping.")
            break

        keyword    = f"{position} {location}"
        search_url = (
            f"https://www.linkedin.com/search/results/people/"
            f"?keywords={quote(keyword)}&origin=GLOBAL_SEARCH_HEADER"
        )

        logger.info(f"\n[SEARCH CONNECTOR] 🔍 Searching: '{keyword}'")
        page.goto(search_url, wait_until="domcontentloaded", timeout=30000)

        # Wait for dynamic content to load
        found = _wait_for_results(page)
        if not found:
            # Try scrolling to trigger lazy load
            page.evaluate("window.scrollTo(0, 400)")
            _rand_delay(2, 3)
            found = _wait_for_results(page, timeout=8000)

        _check_session(page)

        # Scroll down to load more result cards
        for _ in range(2):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            _rand_delay(1, 1.5)

        # Get all Invite/Connect buttons on the page
        btns = _get_connect_buttons(page).all()
        logger.info(f"[SEARCH CONNECTOR] Found {len(btns)} Connect button(s) for '{keyword}'")

        position_sent = 0
        for btn in btns:
            if total_sent >= SEARCH_CONNECT_LIMIT:
                break

            try:
                aria     = btn.get_attribute("aria-label") or ""
                name     = _extract_name_from_aria(aria)
                if not name:
                    name = _extract_name_from_nearby_dom(page, btn)

                # Skip if button is inside a dialog (not a result card)
                in_dialog = btn.evaluate(
                    "el => !!el.closest('[role=\"dialog\"]')"
                )
                if in_dialog:
                    continue

                # Skip navigation/sidebar buttons (text is plain 'Connect' but not in a card)
                btn_text = (btn.inner_text() or "").strip()
                if btn_text.lower() not in ("connect", "+ connect") and "invite" not in aria.lower():
                    continue

                profile_url = _find_profile_url_near_button(page, btn)

                # Dedup check
                if profile_url and _already_sent(db, profile_url):
                    logger.info(f"[SEARCH CONNECTOR] Already contacted '{name}' — skipping")
                    continue

                sent = _send_connection_from_button(page, btn)

                if sent:
                    _save_contact(db, name, profile_url or "", position, location, "search", profile_id)
                    total_sent    += 1
                    position_sent += 1
                    logger.info(
                        f"[SEARCH CONNECTOR] ✅ Sent to '{name}' "
                        f"({total_sent}/{SEARCH_CONNECT_LIMIT}) [{position}]"
                    )
                    _rand_delay(3, 5)
                else:
                    logger.info(f"[SEARCH CONNECTOR] ⚠️  Could not send to '{name}' — skipping")

            except LinkedInSessionExpiredError:
                raise
            except LinkedInWeeklyLimitError:
                logger.warning("[SEARCH CONNECTOR] ⛔ Weekly limit hit — stopping search phase.")
                raise  # stop entire job
            except Exception as exc:
                logger.info(f"[SEARCH CONNECTOR] Error on button: {exc} — skipping")
                continue

        logger.info(f"[SEARCH CONNECTOR] '{position}' done — sent {position_sent} connection(s).")
        _rand_delay(3, 5)

    logger.info(f"[SEARCH CONNECTOR] 🏁 Search phase complete — total sent: {total_sent}")
    return total_sent


# ── Phase 2: My Network "People you may know" ─────────────────────────────────

def _run_network_phase(page, db: Session, location: str, profile_id: int) -> int:
    """
    Navigate to My Network, click 'Show all' for 'People you may know',
    and send up to NETWORK_CONNECT_LIMIT connections.
    """
    total_sent = 0
    GROW_URL   = "https://www.linkedin.com/mynetwork/grow/"

    logger.info(f"\n[SEARCH CONNECTOR] 🌐 Navigating to {GROW_URL}")
    page.goto(GROW_URL, wait_until="domcontentloaded", timeout=30000)
    _rand_delay(3, 4)
    _check_session(page)

    # Scroll to trigger section loading
    page.evaluate("window.scrollTo(0, 600)")
    _rand_delay(1.5, 2)

    # Click the 'Show all' link that points back to /mynetwork/grow/ (the PYMK section)
    try:
        show_all = page.locator("a[href*='mynetwork/grow']").filter(has_text="Show all").first
        if show_all.count() > 0 and show_all.is_visible(timeout=5000):
            logger.info("[SEARCH CONNECTOR] Clicking 'Show all' on People you may know")
            show_all.click()
            _rand_delay(2, 3)
            _check_session(page)
        else:
            logger.info("[SEARCH CONNECTOR] 'Show all' not visible — proceeding with current page")
    except Exception as exc:
        logger.info(f"[SEARCH CONNECTOR] Could not click Show all: {exc}")

    # Wait for connect buttons to appear
    _wait_for_results(page)

    # Scroll to load more
    for _ in range(3):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        _rand_delay(1.5, 2)

    btns = _get_connect_buttons(page).all()
    logger.info(f"[SEARCH CONNECTOR] Found {len(btns)} Connect button(s) on My Network page")

    for btn in btns:
        if total_sent >= NETWORK_CONNECT_LIMIT:
            break

        try:
            aria = btn.get_attribute("aria-label") or ""
            name = _extract_name_from_aria(aria)

            in_dialog = btn.evaluate("el => !!el.closest('[role=\"dialog\"]')")
            if in_dialog:
                continue

            profile_url = _find_profile_url_near_button(page, btn)

            if profile_url and _already_sent(db, profile_url):
                logger.info(f"[SEARCH CONNECTOR] Already contacted '{name}' (network) — skipping")
                continue

            sent = _send_connection_from_button(page, btn)

            if sent:
                _save_contact(db, name, profile_url or "", "", location, "network", profile_id)
                total_sent += 1
                logger.info(
                    f"[SEARCH CONNECTOR] ✅ Network sent to '{name}' "
                    f"({total_sent}/{NETWORK_CONNECT_LIMIT})"
                )
                _rand_delay(3, 5)
            else:
                logger.info(f"[SEARCH CONNECTOR] ⚠️  Could not send to '{name}' (network) — skipping")

        except LinkedInSessionExpiredError:
            raise
        except LinkedInWeeklyLimitError:
            logger.warning("[SEARCH CONNECTOR] ⛔ Weekly limit hit — stopping network phase.")
            raise  # stop entire job
        except Exception as exc:
            logger.info(f"[SEARCH CONNECTOR] Error on network button: {exc} — skipping")
            continue

    logger.info(f"[SEARCH CONNECTOR] 🏁 Network phase complete — total sent: {total_sent}")
    return total_sent


# ── Main entry point ───────────────────────────────────────────────────────────

def run_linkedin_search_and_send_connections(
    positions: list,
    location: str,
    profile_id: int,
    db: Session,
) -> dict:
    """
    Full search-and-connect flow for one LinkedIn profile session.
    Returns a summary dict with counts.
    """
    result = {
        "search_connections_sent":  0,
        "network_connections_sent": 0,
        "session_expired":          False,
        "weekly_limit_reached":     False,
        "error":                    None,
    }

    profile = db.query(ProfileSetting).filter(ProfileSetting.id == profile_id).first()
    if not profile or not profile.session_state:
        result["error"] = "profile_invalid"
        return result

    try:
        session_dict = json.loads(profile.session_state)
    except json.JSONDecodeError:
        result["error"] = "session_invalid"
        return result

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=HEADLESS,
            args=["--disable-blink-features=AutomationControlled"],
        )
        _fp = pick_fingerprint()
        context = browser.new_context(
            storage_state=session_dict,
            viewport=_fp["viewport"],
            user_agent=_fp["user_agent"],
        )
        page = new_stealth_page(context)

        try:
            # Warm-up: verify session
            page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=30000)
            _rand_delay(2, 3)
            _check_session(page)
            logger.info(f"[SEARCH CONNECTOR] ✅ Session valid for profile {profile_id}")

            # Phase 1: search connections
            result["search_connections_sent"] = _run_search_phase(
                page, db, positions, location, profile_id
            )

            # Phase 2: My Network connections
            result["network_connections_sent"] = _run_network_phase(
                page, db, location, profile_id
            )

        except LinkedInSessionExpiredError:
            logger.info("[SEARCH CONNECTOR] ⚠️  Session expired.")
            result["session_expired"] = True

        except LinkedInWeeklyLimitError:
            logger.warning(
                "[SEARCH CONNECTOR] ⛔ Weekly invite limit reached. "
                "No more connections will be sent until next week."
            )
            result["weekly_limit_reached"] = True

        except Exception as exc:
            logger.info(f"[SEARCH CONNECTOR] ❌ Unexpected error: {exc}")
            result["error"] = str(exc)

        finally:
            try:
                profile.session_state = json.dumps(context.storage_state())
                db.commit()
            except Exception:
                pass
            browser.close()

    logger.info(
        f"[SEARCH CONNECTOR] 📊 Summary — "
        f"search_sent={result['search_connections_sent']}, "
        f"network_sent={result['network_connections_sent']}, "
        f"session_expired={result['session_expired']}"
    )
    return result
