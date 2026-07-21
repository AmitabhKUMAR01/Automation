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
from app.utils.logger import logger


# ── Custom exceptions ──────────────────────────────────────────────────────────

class LinkedInWeeklyLimitError(Exception):
    """Raised when LinkedIn blocks the connection due to the weekly invite limit."""
    pass

# ── Constants ──────────────────────────────────────────────────────────────────
SEARCH_CONNECT_LIMIT  = int(os.getenv("SEARCH_CONNECT_LIMIT",  "19"))
NETWORK_CONNECT_LIMIT = int(os.getenv("NETWORK_CONNECT_LIMIT", "20"))

# Max result pages to scan per keyword before moving to the next keyword.
# Increase this so re-runs find people beyond page 1.
SEARCH_MAX_PAGES = int(os.getenv("SEARCH_MAX_PAGES", "5"))

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

    NOTE: Phrases must be specific enough to NOT match the normal
    'Send your invitation to [Name]' modal that appears on every connect click.
    """
    # These phrases appear ONLY in LinkedIn's actual weekly-limit error messages.
    # "invitation to" was intentionally removed — it matches the normal invite modal too.
    error_phrases = [
        "weekly limit",
        "invitation limit",
        "invitation was not sent",
        "try again next week",
        "reached the limit",
        "you've reached",
        "too many invitations",
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

        # Also check if a dialog contains the error — but skip the normal invite modal
        # (it contains 'send without a note', the limit dialog does not)
        dialog = page.locator("div[role='dialog']").first
        if dialog.count() > 0:
            txt = (dialog.inner_text() or "").lower()
            # The normal "Add a note" modal contains 'send without a note' — skip it
            is_invite_modal = "send without a note" in txt or "add a note" in txt
            if not is_invite_modal and any(phrase in txt for phrase in error_phrases):
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


# ── Location filter via LinkedIn UI ──────────────────────────────────────────────────

def _dismiss_linkedin_popups(page) -> None:
    """
    Close any LinkedIn promotional/modal popup that might block the
    filter bar — specifically the Sales Navigator promo card that
    appears inline over search results.
    """
    try:
        # Target the × close button on the Sales Navigator promo
        # and any generic artdeco dismiss buttons.
        close_selectors = [
            "button[aria-label='Dismiss']",
            "button.artdeco-modal__dismiss",
            "svg[data-test-icon='close-medium']",
            "button[data-test-modal-close-btn]",
            # inline promo card close button
            "div.search-norms-disclaimer__dismiss-btn button",
            "button:has-text('Dismiss')",
        ]
        for sel in close_selectors:
            try:
                btns = page.locator(sel).all()
                for btn in btns[:2]:
                    if btn.is_visible(timeout=300):
                        btn.click()
                        _rand_delay(0.2, 0.4)
            except Exception:
                continue

        # Final Escape to close any remaining overlay
        page.keyboard.press("Escape")
        _rand_delay(0.4, 0.6)
    except Exception:
        pass





# ── Phase 1: Search-based connections ─────────────────────────────────────────

import re

def _apply_location_filter_via_ui(page, location: str) -> bool:
    from app.utils.logger import logger
    from app.scraper.linkedin_finder import _rand_delay
    
    logger.info(f"[SEARCH CONNECTOR] Opening location filter panel...")
    opened = False
    for sel in (
        "button.search-reusables__all-filters-pill-button",
        "button:has-text('All filters')",
        "button[aria-label*='Locations']",
        "button:has-text('Locations')",
    ):
        try:
            btn = page.locator(sel).first
            if btn.count() > 0 and btn.is_visible(timeout=3000):
                btn.click(force=True)
                _rand_delay(1, 1.5)
                opened = True
                break
        except Exception:
            continue

    if not opened:
        logger.warning("[SEARCH CONNECTOR] Could not open LinkedIn location filter UI")
        return False

    for btn_sel in (
        "button:has-text('Add a location')",
        "button[aria-label*='Add a location']",
        "button.search-reusables__collection-filter-show-more",
    ):
        try:
            add_btn = page.locator(btn_sel).first
            if add_btn.count() > 0 and add_btn.is_visible(timeout=1000):
                add_btn.click(force=True)
                _rand_delay(0.5, 1)
                break
        except Exception:
            pass

    loc_input = None
    for sel in (
        "input[placeholder*='Add a location']",
        "input[placeholder*='location']",
        "input[aria-label*='Location']",
        "input[aria-label*='location']",
        "div[role='dialog'] input[type='text']",
        "div.search-reusables__filters-bar input[type='text']",
    ):
        try:
            el = page.locator(sel).first
            if el.count() > 0 and el.is_visible(timeout=2000):
                loc_input = el
                break
        except Exception:
            continue

    if not loc_input:
        logger.warning("[SEARCH CONNECTOR] Could not find location input in filter UI")
        page.keyboard.press("Escape")
        return False

    loc_input.click()
    loc_input.fill("")
    loc_input.type(location, delay=80)
    _rand_delay(1.5, 2)

    selected = False
    for sel in (
        "div.basic-typeahead__selectable:visible",
        "li.basic-typeahead__selectable:visible",
        "div.search-typeahead-v2__hit:visible",
        "div[role='option']:visible",
        f"div[role='checkbox']:has-text('{location}')",
        f"div[role='checkbox']:has-text('{location.title()}')",
    ):
        try:
            opt = page.locator(sel).first
            if opt.count() > 0:
                opt.wait_for(state="visible", timeout=3000)
                opt.click(force=True)
                selected = True
                _rand_delay(0.5, 1)
                break
        except Exception:
            continue

    if not selected:
        logger.warning(f"[SEARCH CONNECTOR] No typeahead match found for location '{location}'")
        page.keyboard.press("Escape")
        return False

    clicked_show = page.evaluate("""
        () => {
            const btns = Array.from(document.querySelectorAll('button, div[role="button"], a, span[role="button"]'));
            for (const b of btns) {
                const txt = (b.innerText || '').toLowerCase().trim();
                if (txt === 'show results' || txt.includes('show results') || txt.includes('apply current filters')) {
                    if (b.offsetParent !== null) {
                        b.click();
                        return true;
                    }
                }
            }
            return false;
        }
    """)
    if clicked_show:
        _rand_delay(2, 3)
    else:
        page.keyboard.press("Enter")
        _rand_delay(2, 3)
        
    return True



def _run_search_phase(page, db: Session, positions: list, location: str, profile_id: int) -> int:
    """
    For each position keyword: navigate to LinkedIn people search, applying the
    location by appending it to the search keyword directly in the URL.
    Then paginate up to SEARCH_MAX_PAGES and send up to SEARCH_CONNECT_LIMIT.
    """
    total_sent = 0

    logger.info(f"[SEARCH CONNECTOR] 📍 Location '{location}' will be appended to keyword search.")

    for position in positions:
        if total_sent >= SEARCH_CONNECT_LIMIT:
            logger.info(
                f"[SEARCH CONNECTOR] Reached search limit ({SEARCH_CONNECT_LIMIT}) — stopping."
            )
            break

        position_sent = 0

        # ── Paginate through result pages for this keyword ─────────────────
        base_search_url = ""
        for page_num in range(1, SEARCH_MAX_PAGES + 1):
            if total_sent >= SEARCH_CONNECT_LIMIT:
                break

            if page_num == 1:
                search_url = (
                    f"https://www.linkedin.com/search/results/people/"
                    f"?keywords={quote(position)}"
                    f"&origin=GLOBAL_SEARCH_HEADER"
                )
                logger.info(
                    f"\n[SEARCH CONNECTOR] 🔍 '{position}' — applying '{location}' filter via UI (page 1/{SEARCH_MAX_PAGES})"
                )
                page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
                _wait_for_results(page)
                _check_session(page)
                _dismiss_linkedin_popups(page)
                
                # Apply filter
                _apply_location_filter_via_ui(page, location)
                
                # Verify / wait for results
                if not _wait_for_results(page):
                    logger.warning("[SEARCH CONNECTOR] ⚠️ Results didn't load immediately, reloading page with applied filter...")
                    page.reload(wait_until="domcontentloaded", timeout=30000)
                    _wait_for_results(page)
                    
                # Cache the URL (which now has geoUrn) for subsequent pages
                base_search_url = page.url
            else:
                # Remove any existing page parameter if present
                base = base_search_url.split("&page=")[0]
                page_url = f"{base}&page={page_num}"
                logger.info(
                    f"\n[SEARCH CONNECTOR] 🔍 '{position}' + '{location}' — page {page_num}/{SEARCH_MAX_PAGES}"
                )
                page.goto(page_url, wait_until="domcontentloaded", timeout=30000)
                _wait_for_results(page)
                _check_session(page)

            # Scroll to load lazy-rendered result cards
            for _ in range(2):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                _rand_delay(1, 1.5)

            btns = _get_connect_buttons(page).all()
            logger.info(
                f"[SEARCH CONNECTOR] Found {len(btns)} Connect button(s) on page {page_num}"
            )

            # No connect buttons → page exhausted (all Pending / no results)
            if not btns:
                logger.info(
                    f"[SEARCH CONNECTOR] No Connect buttons on page {page_num} "
                    f"— stopping pagination for '{position}'"
                )
                break

            page_sent = 0
            for btn in btns:
                if total_sent >= SEARCH_CONNECT_LIMIT:
                    break

                try:
                    if not btn.is_visible(timeout=2000):
                        continue
                    aria = btn.get_attribute("aria-label", timeout=2000) or ""
                    name = _extract_name_from_aria(aria)
                    if not name:
                        name = _extract_name_from_nearby_dom(page, btn)

                    # Skip buttons inside a dialog (not a result card)
                    in_dialog = btn.evaluate(
                        "el => !!el.closest('[role=\"dialog\"]')"
                    )
                    if in_dialog:
                        continue

                    # Skip navigation/sidebar Connect buttons
                    btn_text = (btn.inner_text() or "").strip()
                    if (
                        btn_text.lower() not in ("connect", "+ connect")
                        and "invite" not in aria.lower()
                    ):
                        continue

                    profile_url = _find_profile_url_near_button(page, btn)

                    # Dedup — skip anyone we've already contacted
                    if profile_url and _already_sent(db, profile_url):
                        logger.info(
                            f"[SEARCH CONNECTOR] Already contacted '{name}' — skipping"
                        )
                        continue

                    sent = _send_connection_from_button(page, btn)

                    if sent:
                        _save_contact(
                            db, name, profile_url or "", position, location, "search", profile_id
                        )
                        total_sent    += 1
                        position_sent += 1
                        page_sent     += 1
                        logger.info(
                            f"[SEARCH CONNECTOR] ✅ Sent to '{name}' "
                            f"({total_sent}/{SEARCH_CONNECT_LIMIT}) "
                            f"[{position} · p{page_num}]"
                        )
                        _rand_delay(3, 5)
                    else:
                        logger.info(
                            f"[SEARCH CONNECTOR] ⚠️  Could not send to '{name}' — skipping"
                        )

                except LinkedInSessionExpiredError:
                    raise
                except LinkedInWeeklyLimitError:
                    logger.warning(
                        "[SEARCH CONNECTOR] ⛔ Weekly limit hit — stopping search phase."
                    )
                    raise
                except Exception as exc:
                    logger.info(f"[SEARCH CONNECTOR] Error on button: {exc} — skipping")
                    continue

            logger.info(
                f"[SEARCH CONNECTOR] Page {page_num} done — "
                f"{page_sent} new connection(s) sent this page"
            )

            # Sparse/exhausted page — stop paginating
            if page_sent == 0 and len(btns) < 5:
                logger.info(
                    f"[SEARCH CONNECTOR] Sparse/exhausted results on page {page_num} "
                    f"— stopping pagination for '{position}'"
                )
                break

            _rand_delay(2, 3)   # polite delay between pages

        logger.info(
            f"[SEARCH CONNECTOR] '{position}' complete — "
            f"{position_sent} connection(s) sent."
        )
        _rand_delay(3, 5)

    logger.info(
        f"[SEARCH CONNECTOR] 🏁 Search phase complete — total sent: {total_sent}"
    )
    return total_sent


def _find_target_show_all_link(page):
    """
    Find the 'Show all' link STRICTLY for 'People you may know based on your recent activity'.
    Returns (locator, heading_title) or (None, "").
    """
    try:
        links = page.locator(
            "a:has-text('Show all'), "
            "button:has-text('Show all'), "
            "a[aria-label*='Show all']"
        ).all()

        for sa in links:
            try:
                lbl = sa.get_attribute("aria-label") or ""
                href = sa.get_attribute("href") or ""

                # Ignore invitation manager links
                if "invitation" in lbl.lower() or "invitation-manager" in href.lower():
                    continue

                heading = sa.evaluate("""
                    el => {
                        let node = el;
                        for (let i = 0; i < 8; i++) {
                            if (!node || !node.parentElement) break;
                            node = node.parentElement;
                            const h = node.querySelector("h1, h2, h3, h4");
                            if (h) return (h.innerText || '').trim();
                        }
                        return "";
                    }
                """) or ""

                text_combo = f"{lbl} {heading}".lower()

                # STRICT MATCH ONLY: "recent activity" / "people you may know based on your recent activity"
                if "recent activity" in text_combo:
                    title = heading or lbl or "People you may know based on your recent activity"
                    return sa, title
            except Exception:
                pass
    except Exception as exc:
        logger.info(f"[SEARCH CONNECTOR] Error finding target Show all link: {exc}")
    return None, ""


def _find_recent_activity_section(page):
    """
    Find section element strictly matching heading 'People you may know based on your recent activity'.
    """
    try:
        headers = page.locator("h1, h2, h3, h4").all()
        for h in headers:
            txt = (h.inner_text() or "").strip()
            if "recent activity" in txt.lower():
                sec = h.locator("xpath=ancestor::section[1]")
                if sec.count() > 0:
                    return sec
    except Exception:
        pass
    return None


def _scroll_page_container(page, amount: int = 700) -> None:
    """
    Scroll LinkedIn's actual layout container (#workspace / main / .scaffold-layout__main)
    as well as window/body, and dispatch mouse wheel event so lazy-loading triggers properly.
    """
    try:
        page.evaluate(
            """
            (amt) => {
                const main = document.querySelector('#workspace, main, div.scaffold-layout__main, .scaffold-layout__content');
                if (main && main.scrollHeight > main.clientHeight) {
                    main.scrollTop += amt;
                }
                window.scrollBy(0, amt);
            }
            """,
            amount,
        )
        try:
            page.mouse.wheel(0, amount)
        except Exception:
            pass
    except Exception:
        pass


# ── Phase 2: My Network "People you may know based on your recent activity" ───

def _run_network_phase(page, db: Session, location: str, profile_id: int) -> int:
    """
    Navigate to My Network, click 'Show all' STRICTLY for 'People you may know based on your recent activity',
    and send up to NETWORK_CONNECT_LIMIT connections strictly to cards from this section.
    """
    total_sent = 0
    GROW_URL   = "https://www.linkedin.com/mynetwork/grow/"

    logger.info(f"\n[SEARCH CONNECTOR] 🌐 Navigating to {GROW_URL}")
    page.goto(GROW_URL, wait_until="domcontentloaded", timeout=30000)
    _rand_delay(3, 4)
    _check_session(page)

    # Progressive scroll loop to trigger LinkedIn lazy loading and discover 'People you may know based on your recent activity'
    show_all_link = None
    section_heading = ""
    section_container = None
    clicked_show_all = False

    logger.info("[SEARCH CONNECTOR] 🔍 Scanning for 'People you may know based on your recent activity'...")
    for attempt in range(1, 9):
        # Check for 'Show all' link matching "recent activity"
        show_all_link, section_heading = _find_target_show_all_link(page)
        if show_all_link and show_all_link.is_visible():
            break

        # Check for inline section container matching "recent activity"
        section_container = _find_recent_activity_section(page)
        if section_container:
            break

        # Scroll down progressively to trigger lazy loading of deeper sections
        _scroll_page_container(page, 700)
        _rand_delay(1.5, 2.0)

    if show_all_link and show_all_link.is_visible():
        logger.info(f"[SEARCH CONNECTOR] Strictly matched 'Show all' for: '{section_heading}' — clicking")
        try:
            show_all_link.click(force=True)
            _rand_delay(2, 3)
            _check_session(page)
            clicked_show_all = True
        except Exception as exc:
            logger.info(f"[SEARCH CONNECTOR] Could not click Show all: {exc}")
    elif section_container:
        logger.info("[SEARCH CONNECTOR] Found 'People you may know based on your recent activity' inline section on page")
    else:
        logger.warning(
            "[SEARCH CONNECTOR] ⚠️ Section 'People you may know based on your recent activity' "
            "not found after scanning page — skipping network phase strictly."
        )
        return 0

    # Wait for connect buttons to appear
    _wait_for_results(page)

    # Scroll to load lazy items
    for _ in range(3):
        _scroll_page_container(page, 800)
        _rand_delay(1.5, 2)

    # Get Connect buttons (scoped strictly to section_container if inline, or full expanded page if Show All clicked)
    if clicked_show_all:
        btns = _get_connect_buttons(page).all()
    else:
        btns = section_container.locator(
            "button[aria-label*='Invite'][aria-label*='connect'], "
            "button[aria-label*='Connect'], "
            "button:has-text('Connect'), "
            "a:has-text('Connect')"
        ).all()

    logger.info(f"[SEARCH CONNECTOR] Found {len(btns)} Connect button(s) strictly for 'People you may know based on your recent activity'")

    for btn in btns:
        if total_sent >= NETWORK_CONNECT_LIMIT:
            break

        try:
            if not btn.is_visible(timeout=2000):
                continue
            aria = btn.get_attribute("aria-label", timeout=2000) or ""
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
        context = browser.new_context(
            storage_state=session_dict,
            viewport={"width": 1400, "height": 900},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

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
            try:
                browser.close()
            except Exception:
                pass   # silences "Connection closed" error on Ctrl+C

    logger.info(
        f"[SEARCH CONNECTOR] 📊 Summary — "
        f"search_sent={result['search_connections_sent']}, "
        f"network_sent={result['network_connections_sent']}, "
        f"session_expired={result['session_expired']}"
    )
    return result
