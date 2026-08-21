from app.utils.logger import logger
import os
import re
import time
import random
import uuid as _uuid
from urllib.parse import quote

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from sqlalchemy.orm import Session

from app.config.database import SessionLocal
from app.models.business_client import Business_Client
from app.models.linkedin_contact import LinkedinContact

import json
from app.models.profile_setting import ProfileSetting

HEADLESS = os.getenv("LINKEDIN_HEADLESS", "false").lower() == "true"

# Seconds to wait between each company in batch mode (reduces bot detection risk)
BETWEEN_COMPANIES_DELAY = int(os.getenv("LINKEDIN_BETWEEN_COMPANIES_DELAY", "60"))

TARGET_TITLES = [
    "CEO", "Chief Executive",
    "Founder", "Co-Founder", "Co Founder",
    "CTO", "Chief Technology",
    "COO", "Chief Operating",
    "VP", "Vice President",
    "Director", "Managing Director",
]

HIGH_KEYWORDS   = {"ceo", "founder", "co-founder", "cto", "coo", "vp", "director", "managing director"}
MEDIUM_KEYWORDS = {"chief", "vice", "president", "executive", "operating", "technology"}


class LinkedInSessionExpiredError(Exception):
    """Raised when LinkedIn redirects to login/checkpoint page."""
    pass


## Helpers
def _rand_delay(lo: float = 1.5, hi: float = 3.5):
    time.sleep(random.uniform(lo, hi))


def _handle_welcome_back_page(page) -> bool:
    """
    Handle LinkedIn's 'Welcome Back' page.

    This page appears when a saved session is loaded from a different
    IP/machine. LinkedIn recognises the account (shows the user's photo
    and name) but requires a single click on the account tile to continue.

    Strategy — try selectors in order of specificity:
      1. Explicit aria-label / data-aut-id attributes LinkedIn has used historically
      2. Class-based selectors observed in different LinkedIn UI versions
      3. The profile-tile div/button (contains user's avatar/photo — not the 'Sign in using
         another account' link which is a separate element below the tile)
      4. First button in the sign-in form

    NOTE: We intentionally do NOT fall back to `button:has-text('Sign in')` because on
    this page that matches the "Sign in using another account" button, which redirects to
    `/checkpoint/rm/sign-in-another-account` — an empty sign-in form — not back to feed.

    Returns True if the click succeeded and we navigated to a valid LinkedIn page (/feed etc).
    Returns False if every selector fails (caller will raise SessionExpiredError).
    """
    logger.info("[LINKEDIN] 👋 'Welcome Back' page detected — attempting auto-click on account tile...")

    # Selectors tried in priority order (most specific → most generic).
    # ⚠️  Do NOT add `button:has-text('Sign in')` here — it matches
    #     "Sign in using another account" which leads to /checkpoint, not /feed.
    account_selectors = [
        # LinkedIn-specific data attributes (most reliable when present)
        "[data-aut-id='account-picker-user-account']",
        "button[aria-label*='Sign in as']",
        "button[aria-label*='sign in as']",
        # Class-based selectors from different LinkedIn UI versions
        "button.sign-in-card--account",
        "div.sign-in-modal__account-btn",
        ".base-sign-in-hide-if-logged-in button",
        # Profile tile: a clickable element containing an <img> (user avatar) —
        # this is the user's own account card, not the "Sign in using another account" link.
        "div.sign-in-form__account-picker img",      # click the avatar inside the tile
        "button:has(img)",                            # button wrapping the profile image
        "div[role='button']:has(img)",                # div tile wrapping the profile image
        # Continue button (sometimes used instead of profile tile click)
        "button:has-text('Continue')",
        # First button in the form — safe only if the "Sign in using another account"
        # link is rendered as an anchor (<a>), not a <button>.
        "form button:first-of-type",
    ]

    for selector in account_selectors:
        try:
            btn = page.locator(selector).first
            if btn.count() > 0 and btn.is_visible(timeout=2000):
                logger.info(f"[LINKEDIN] 🖱️  Clicking account tile ({selector})")
                btn.click()
                # Wait until we reach a real LinkedIn page.
                # We also exclude /checkpoint because clicking "Sign in using another
                # account" lands there — that is NOT a successful bypass.
                page.wait_for_url(
                    lambda u: (
                        "/login" not in u
                        and "/uas" not in u
                        and "/checkpoint" not in u
                    ),
                    timeout=20000,
                )
                final_url = page.url
                logger.info(f"[LINKEDIN] ✅ Welcome Back bypassed → {final_url}")
                _rand_delay(1.5, 2.5)   # brief pause after redirect
                return True
        except Exception:
            continue   # try the next selector

    logger.warning("[LINKEDIN] ⚠️  Could not find/click account tile on Welcome Back page.")
    return False


def _check_session(page) -> None:
    """
    Validate the browser is still on a legitimate LinkedIn page.

    Handles three distinct cases:
      1. 'Welcome Back' page (different IP/machine) — auto-click the account tile.
      2. Auto-signin redirect ("We're signing you in") — wait for completion.
      3. Real expiry / challenge — /login (no redirect), /checkpoint, /authwall.
    """
    url = page.url.lower()

    # ── Case 1: LinkedIn 'Welcome Back' page (cross-machine session reuse) ─────
    # URL: /login?session_redirect=...&skipRedirect=true
    # LinkedIn knows the account but needs a click to confirm the device.
    if "/login" in url and "session_redirect" in url:
        try:
            body_text = page.locator("body").inner_text(timeout=3000).lower()
        except Exception:
            body_text = ""

        if "welcome back" in body_text:
            # Auto-click the account tile to continue
            clicked = _handle_welcome_back_page(page)
            if not clicked:
                raise LinkedInSessionExpiredError(
                    "\n\n⚠️  LinkedIn showed 'Welcome Back' but auto-click failed.\n"
                    "   This usually means the session needs to be refreshed on this machine.\n"
                    "   Re-run:  python linkedin_login.py --profile <id>\n"
                )
            # Successfully clicked through — re-read the current URL and fall through
            url = page.url.lower()

        else:
            # ── Case 2: Regular auto-signin redirect ──────────────────────────
            # LinkedIn is silently re-authenticating. Wait for it to complete.
            logger.info("[LINKEDIN] ⏳ Auto-signin redirect detected — waiting for completion...")
            try:
                page.wait_for_url(
                    lambda u: (
                        "/feed" in u
                        or "/jobs" in u
                        or "/mynetwork" in u
                        or "/checkpoint" in u
                        or "/authwall" in u
                        or ("/login" in u and "session_redirect" not in u)
                    ),
                    timeout=20000,
                )
                url = page.url.lower()
                logger.info(f"[LINKEDIN] ↩  Auto-signin completed → {page.url}")
            except PlaywrightTimeout:
                raise LinkedInSessionExpiredError(
                    "\n\n⚠️  LinkedIn auto-signin timed out (stuck on sign-in page).\n"
                    "   Re-run:  python linkedin_login.py --profile <id>\n"
                )

    # ── Case 3: Check page.title() — catches mid-navigation context destruction
    try:
        title = page.title().lower()
    except Exception:
        # "Execution context was destroyed" = LinkedIn did a forced redirect mid-page
        raise LinkedInSessionExpiredError(
            "\n\n⚠️  LinkedIn issued a mid-navigation challenge!\n"
            "   Re-run:  python linkedin_login.py --profile <id>\n"
        )

    # ── Case 4: Real expired / challenged states ──────────────────────────────
    is_real_login = "/login" in url and "session_redirect" not in url
    if (
        is_real_login
        or "/checkpoint" in url
        or "/authwall" in url
        or "sign in" in title
        or "join linkedin" in title
    ):
        raise LinkedInSessionExpiredError(
            "\n\n⚠️  LinkedIn session expired or account challenged!\n"
            "   Re-run:  python linkedin_login.py --profile <id>\n"
            "   Then retry the LinkedIn search.\n"
        )


def _dismiss_feed_popups(page) -> None:
    """
    Dismiss any promotional / upsell modal or overlay that LinkedIn injects
    on the feed page after login.  Currently handles:

    * LinkedIn Premium trial offer  ("Try Premium for ₹0", "Try Premium for free",
      "looking to drive more leads?", etc.)
    * Sales Navigator promotional card
    * Cookie / GDPR consent banners
    * Generic artdeco modal dismiss buttons

    This is a best-effort, fire-and-forget function — it never raises.
    """
    close_selectors = [
        # Premium upsell modal dismiss / close button
        "button[aria-label='Dismiss']",
        "button[aria-label='dismiss']",
        "button.artdeco-modal__dismiss",
        "button[data-test-modal-close-btn]",
        # Generic ✕ / close icons used across LinkedIn modals
        "svg[data-test-icon='close-medium'] >> xpath=..",   # parent button of ✕ icon
        "button:has(svg[data-test-icon='close-medium'])",
        "button:has(svg[data-test-icon='close-small'])",
        # Inline promo-card dismiss
        "div.search-norms-disclaimer__dismiss-btn button",
        # Cookie / GDPR banner
        "button[action-type='DENY']",
        "button:has-text('Reject')",
        "button:has-text('Accept')",
        # Any button explicitly labelled 'Close' or 'Dismiss' (case-insensitive)
        "button:has-text('Close')",
        "button:has-text('Dismiss')",
        # Premium modal: the large ✕ in the top-right corner
        ".premium-upsell-modal button[aria-label*='Close']",
        ".premium-upsell-modal button[aria-label*='Dismiss']",
        # Catch-all: any visible ✕ button inside a modal/dialog
        "div[role='dialog'] button[aria-label*='Dismiss']",
        "div[role='dialog'] button[aria-label*='Close']",
    ]

    dismissed_any = False
    for sel in close_selectors:
        try:
            btns = page.locator(sel).all()
            for btn in btns[:2]:          # at most 2 per selector to avoid loops
                try:
                    if btn.is_visible(timeout=500):
                        btn.click(force=True)
                        _rand_delay(0.3, 0.6)
                        dismissed_any = True
                        logger.info(f"[LINKEDIN] 🗙  Dismissed popup via: {sel}")
                except Exception:
                    pass
        except Exception:
            continue

    # If nothing was found via click, try Escape as a last resort
    if not dismissed_any:
        try:
            # Only press Escape if a dialog/modal is actually open
            if page.locator("div[role='dialog']:visible").count() > 0:
                page.keyboard.press("Escape")
                _rand_delay(0.3, 0.5)
                logger.info("[LINKEDIN] 🗙  Pressed Escape to dismiss open dialog")
        except Exception:
            pass


def _compute_confidence(headline: str) -> str:
    hl = headline.lower()
    for kw in HIGH_KEYWORDS:
        if kw in hl:
            return "high"
    for kw in MEDIUM_KEYWORDS:
        if kw in hl:
            return "medium"
    return "low"


def _clean_profile_url(href: str) -> str | None:
    """Return a clean linkedin.com/in/<slug> URL or None if not a person profile."""
    if not href:
        return None
    if href.startswith("/"):
        href = "https://www.linkedin.com" + href
    match = re.search(r'(https://www\.linkedin\.com/in/[a-zA-Z0-9\-_%]+)', href)
    if match:
        return match.group(1).rstrip("/")
    return None


def _update_client_status(client_id: int, status: str, searched: bool, db: Session):
    client = db.query(Business_Client).filter(Business_Client.id == client_id).first()
    if client:
        client.linkedin_search_status = status  # type: ignore
        if searched:
            client.is_linkedin_searched = True  # type: ignore
        db.commit()


## Main scraper function
def find_linkedin_playwright(company: str, business_client_id: int, db: Session, profile_id: int = 1) -> dict:
    ## fetch business client
    client = db.query(Business_Client).filter(Business_Client.id == business_client_id).first()
    if not client:
        return {"error": "business_client_not_found"}

    if client.is_linkedin_searched:
        logger.info(f"[LINKEDIN] ⏭  Skipping '{company}' — already searched (status: {client.linkedin_search_status})")
        return {"skipped": True, "status": client.linkedin_search_status}

    summary: dict[str, str | int | None] = {
        "company":          company,
        "company_page_url": None,
        "contacts_found":   0,
        "contacts_saved":   0,
        "status":           "pending",
    }

    logger.info(f"\n[LINKEDIN] 🔍 Starting search for: {company} using profile ID {profile_id}")

    profile = db.query(ProfileSetting).filter(ProfileSetting.id == profile_id).first()
    if not profile or not profile.session_state:
        logger.info(f"[LINKEDIN] ❌ Profile {profile_id} not found or has no session state.")
        return {"error": "profile_invalid"}
    
    try:
        session_dict = json.loads(profile.session_state)
    except json.JSONDecodeError:
        logger.info(f"[LINKEDIN] ❌ Profile {profile_id} session state is invalid JSON.")
        return {"error": "session_invalid"}

    with sync_playwright() as pw:
        ## launch browser
        browser = pw.chromium.launch(
            headless=HEADLESS,
            slow_mo=random.randint(600, 1000),
            args=["--disable-blink-features=AutomationControlled"],
        )

        ## new context
        context = browser.new_context(
            storage_state=session_dict,
            viewport={"width": 1400, "height": 900},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        ## new page
        page = context.new_page()

        try:
            ## Verify session
            logger.info("[LINKEDIN] Checking session …")
            page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=45000)
            _rand_delay(2, 4)
            _check_session(page)
            _dismiss_feed_popups(page)   # close any Premium/promo modal before proceeding
            logger.info("[LINKEDIN] ✅ Session valid")

            ## Search for the company
            search_url = (
                f"https://www.linkedin.com/search/results/companies/"
                f"?keywords={quote(company)}"
            )
            logger.info(f"[LINKEDIN] Searching companies: {search_url}")
            page.goto(search_url, wait_until="domcontentloaded", timeout=45000)
            _rand_delay(2, 4)
            _check_session(page)

            # Find first company result link
            company_links = page.locator("a[href*='/company/']")
            if company_links.count() == 0:
                logger.info(f"[LINKEDIN] ❌ No company found for '{company}'")
                _update_client_status(business_client_id, "company_not_found", searched=True, db=db)
                summary["status"] = "company_not_found"
                return summary

            # Get company page URL — prefer results card links, skip sidebar/ads
            company_page_url = None
            for i in range(min(company_links.count(), 8)):
                href = company_links.nth(i).get_attribute("href") or ""
                href = href.split("?")[0].rstrip("/")
                if re.search(r'/company/[a-zA-Z0-9\-_]+$', href):
                    if href.startswith("/"):
                        href = "https://www.linkedin.com" + href
                    company_page_url = href
                    break

            if not company_page_url:
                logger.info(f"[LINKEDIN] ❌ Could not resolve company page URL for '{company}'")
                _update_client_status(business_client_id, "company_not_found", searched=True, db=db)
                summary["status"] = "company_not_found"
                return summary

            logger.info(f"[LINKEDIN] ✅ Company page: {company_page_url}")
            summary["company_page_url"] = company_page_url

            # Update social_links with the company page
            if client.social_links:
                client.social_links = {**client.social_links, "linkedin": company_page_url}  # type: ignore
            else:
                client.social_links = {"linkedin": company_page_url}  # type: ignore
            db.commit()

            ## Search People at the company
            saved_urls: set[str] = set()
            contacts_saved = 0
            contacts_found = 0

            for title in TARGET_TITLES:
                logger.info(f"\n[LINKEDIN] 👤 Searching people — title: '{title}'")

                try:
                    people_url = (
                        f"https://www.linkedin.com/search/results/people/"
                        f"?keywords={quote(title)}"
                        f"&company={quote(company)}"
                        f"&origin=FACETED_SEARCH"
                    )
                    page.goto(people_url, wait_until="domcontentloaded", timeout=45000)
                    _rand_delay(2, 4)
                    _check_session(page)  # session check after goto — always propagates

                    # Wait for results container or cards
                    try:
                        page.wait_for_selector(
                            "ul.reusable-search__entity-result-list, "
                            ".search-results-container, "
                            "div[role='listitem'], "
                            "div.search-no-results",
                            timeout=10000
                        )
                    except PlaywrightTimeout:
                        logger.info(f"[LINKEDIN] ⚠️  No results container for title '{title}', skipping")
                        _rand_delay(2, 4)
                        continue

                    # Collect all result cards (supports old layout and new SDUI layout)
                    result_cards = page.locator(
                        "li.reusable-search__result-container, "
                        "li[class*='result-container'], "
                        "div[role='listitem']:has(a[href*='/in/'])"
                    )
                    card_count = result_cards.count()
                    logger.info(f"[LINKEDIN] Found {card_count} cards for '{title}'")

                    for i in range(card_count):
                        card = result_cards.nth(i)
                        try:
                            # Extract profile URL
                            link_el = card.locator("a[href*='/in/']").first
                            if link_el.count() == 0:
                                continue
                            raw_href = link_el.get_attribute("href") or ""
                            profile_url = _clean_profile_url(raw_href)
                            if not profile_url:
                                continue

                            # Skip duplicates within this run
                            if profile_url in saved_urls:
                                continue

                            # Skip duplicates already in DB for this client
                            existing = db.query(LinkedinContact).filter(
                                LinkedinContact.business_client_id == business_client_id,
                                LinkedinContact.profile_url == profile_url,
                            ).first()
                            if existing:
                                saved_urls.add(profile_url)
                                continue

                            # Extract text lines for robust parsing regardless of classes
                            text_lines = [line.strip() for line in card.inner_text().split('\n') if line.strip()]
                            name = ""
                            headline = title

                            if text_lines:
                                # The first text element is typically the name
                                name = text_lines[0]
                                if not name or "linkedin member" in name.lower():
                                    continue  # Private profile

                                # The headline is usually the 2nd or 3rd text element
                                if len(text_lines) > 2:
                                    if "\u2022" in text_lines[1] or "degree" in text_lines[1].lower():
                                        headline = text_lines[2]
                                    else:
                                        headline = text_lines[1]
                                elif len(text_lines) > 1:
                                    headline = text_lines[1]
                            else:
                                continue


                            confidence = _compute_confidence(headline)
                            contacts_found += 1

                            contact = LinkedinContact(
                                uuid               = str(_uuid.uuid4()),
                                business_client_id = business_client_id,
                                name               = name,
                                job_title          = headline or title,
                                profile_url        = profile_url,
                                match_confidence   = confidence,
                                connection_sent    = False,
                            )
                            db.add(contact)
                            db.commit()
                            saved_urls.add(profile_url)
                            contacts_saved += 1
                            logger.info(f"[LINKEDIN]   💾 Saved: {name} | {headline} | {confidence} | {profile_url}")

                        except Exception as card_err:
                            logger.info(f"[LINKEDIN] ⚠️  Error reading card {i}: {card_err}")
                            continue

                except LinkedInSessionExpiredError:
                    raise  # always propagate — stops the entire batch

                except PlaywrightTimeout:
                    logger.info(f"[LINKEDIN] ⏲  Timeout on people search for '{title}' — skipping title")
                    _rand_delay(4, 8)  # longer pause after a timeout
                    continue

                except Exception as title_err:
                    logger.info(f"[LINKEDIN] ⚠️  Error searching title '{title}': {title_err} — skipping")
                    _rand_delay(2, 4)
                    continue

                # Polite delay between each title search
                _rand_delay(3, 6)

            ## Finalise status
            summary["contacts_saved"] = contacts_saved
            summary["contacts_found"] = contacts_found
            if contacts_saved == 0:
                status = "no_contacts_found"
            else:
                status = "completed"

            _update_client_status(business_client_id, status, searched=True, db=db)
            summary["status"] = status
            logger.info(f"\n[LINKEDIN] ✅ Done for '{company}' — saved {contacts_saved} contacts | status: {status}")

            # Save refreshed session cookies so the next company starts with fresh auth
            try:
                new_state = context.storage_state()
                profile.session_state = json.dumps(new_state)
                db.commit()
                logger.info(f"[LINKEDIN] 💾 Session state refreshed and saved for profile {profile_id}.")
            except Exception as _se:
                logger.info(f"[LINKEDIN] ⚠️  Could not save session state: {_se}")

        except LinkedInSessionExpiredError as e:
            logger.info(str(e))
            _update_client_status(business_client_id, "session_expired", searched=False, db=db)
            summary["status"] = "session_expired"
            raise   # propagate so service layer can stop the batch

        except Exception as exc:
            logger.info(f"[LINKEDIN] ❌ Unexpected error for '{company}': {exc}")
            _update_client_status(business_client_id, "error", searched=True, db=db)
            summary["status"] = "error"
            summary["error"]  = str(exc)

        finally:
            browser.close()

    return summary