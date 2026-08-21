from __future__ import annotations
from app.utils.logger import logger
import time
import random
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout, Locator
from app.models.linkedin_contact import LinkedinContact
from app.models.linkedin_search_contact import LinkedinSearchContact
from app.scraper.linkedin_finder import (
    HEADLESS,
    LinkedInSessionExpiredError,
    _rand_delay,
    _check_session,
    _dismiss_feed_popups,
)
import json
from app.models.profile_setting import ProfileSetting
from sqlalchemy.orm import Session


def _find_message_button(page, contact_name: str):
    """
    Try multiple strategies to locate the Message button on a LinkedIn profile.
    Returns the first matching Locator element, or None if not found.

    LinkedIn renders different markup depending on connection degree and viewport,
    so we cascade through specific → general selectors.
    """
    strategies = [
        # Strategy 1: aria-label contains the contact's first name (most specific)
        f"button[aria-label*='Message'][aria-label*='{contact_name.split()[0]}']",
        # Strategy 2: generic aria-label patterns
        "button[aria-label='Message']",
        "button[aria-label*='Message']",
        # Strategy 3: data-control-name (older LinkedIn markup)
        "button[data-control-name='message']",
        "a[data-control-name='message']",
        # Strategy 4: text-based — scoped to profile top section first
        "div.pv-top-card-v2-ctas button:has-text('Message')",
        "div.pvs-profile-actions button:has-text('Message')",
        "div.ph5 button:has-text('Message')",
        # Strategy 5: broadest fallback — any button on the page with exact text
        "button:has-text('Message')",
    ]

    for selector in strategies:
        try:
            loc = page.locator(selector).first
            if loc.count() > 0 and loc.is_visible():
                logger.info(f"[DM SENDER] 🔍 Message button found via: {selector!r}")
                return loc
        except Exception:
            continue

    # Debug: dump all visible button labels so we can diagnose future failures
    logger.info("[DM SENDER] 🔎 Dumping visible buttons for diagnosis:")
    try:
        buttons = page.locator("button").all()
        for btn in buttons[:20]:
            try:
                label = btn.get_attribute("aria-label") or btn.inner_text().strip()[:50]
                if label:
                    logger.info(f"  → {label!r}")
            except Exception:
                pass
    except Exception:
        pass

    return None


def _fill_recipient(page, name: str, job_title: str = "") -> bool:
    """
    Type the contact's full name into the To: field of the compose modal,
    wait for the autocomplete dropdown, then pick the best-matching result
    by scoring candidates against both name and job title.

    Parameters
    ----------
    name      : Contact's full name (e.g. "Mahima Upadhyay")
    job_title : Contact's job title used to disambiguate same-name matches
                (e.g. "Operations Manager at HangingPanda")

    Returns True if a recipient chip was locked in, False otherwise.
    """
    to_input = page.locator(
        "input.msg-connections-typeahead__search-field, "
        "input[placeholder*='Type a name' i], "
        "input[placeholder*='name or multiple names' i], "
        "div.msg-connections-typeahead input"
    ).first

    try:
        to_input.wait_for(state="visible", timeout=6000)
    except PlaywrightTimeout:
        logger.info("[DM SENDER] ⚠️  To: input field not found in compose modal.")
        return False

    to_input.click()
    _rand_delay(0.3, 0.5)

    # ── Search strategy: try full name first, fall back to first name ──────────
    search_terms = [name.strip(), name.strip().split()[0]]

    for search_term in search_terms:
        # Clear and retype the search field
        to_input.fill("")   # clears existing text
        to_input.type(search_term, delay=80)
        _rand_delay(1.5, 2.5)  # wait for autocomplete to load

        # Collect all visible autocomplete list items
        result_selectors = [
            "li.msg-connections-typeahead__list-item",
            "li[data-test-typeahead-list-item]",
            "li[role='option']",
            ".msg-connections-typeahead__list li",
        ]

        candidates = []
        for sel in result_selectors:
            try:
                items = page.locator(sel).all()
                if items:
                    candidates = items
                    break
            except Exception:
                continue

        if not candidates:
            logger.info(f"[DM SENDER] ⚠️  No autocomplete results for {search_term!r} — trying fallback.")
            continue

        # ── Score each candidate ───────────────────────────────────────────────
        name_lower  = name.strip().lower()
        first_lower = name.strip().split()[0].lower()
        title_lower = job_title.strip().lower()

        best_item  = None
        best_score = -1

        for item in candidates[:8]:
            try:
                text = item.inner_text().strip().lower()
            except Exception:
                continue

            score = 0

            # Full name exact match — highest confidence
            if name_lower in text:
                score += 10

            # First name match (partial) — medium confidence
            if first_lower in text:
                score += 4

            # Job title words overlap — disambiguation bonus
            if title_lower:
                title_words = [w for w in title_lower.split() if len(w) > 3]
                matched_words = sum(1 for w in title_words if w in text)
                score += matched_words * 2

            # Penalise results that look like company pages (no degree indicator)
            if "• 1st" in item.inner_text() or "• 2nd" in item.inner_text():
                score += 3  # bonus: this is a real person connection

            logger.info(f"[DM SENDER] 🔍  Candidate score={score}: {item.inner_text().strip()[:80]!r}")

            if score > best_score:
                best_score = score
                best_item  = item

        if best_item is None or best_score < 4:
            logger.info(f"[DM SENDER] ⚠️  No confident match found for {name!r} (best score={best_score}).")
            continue

        # Click the best match
        try:
            best_item.click(force=True)
            _rand_delay(0.5, 1.0)
            logger.info(f"[DM SENDER] 👤 Recipient selected (score={best_score}): {best_item.inner_text().strip()[:80]!r}")
        except Exception as e:
            logger.info(f"[DM SENDER] ⚠️  Failed to click candidate: {e}")
            continue

        # Confirm the recipient pill appeared
        pill = page.locator(
            ".msg-connections-typeahead__pills .msg-compose__pill, "
            ".msg-connections-typeahead-pill__close-button, "
            "[class*='typeahead'] [class*='pill']"
        ).first

        _rand_delay(0.3, 0.6)
        if pill.count() > 0:
            logger.info(f"[DM SENDER] ✅ Recipient chip confirmed: {pill.inner_text().strip()!r}")
            return True

        logger.info("[DM SENDER] ⚠️  Chip not visible after click — retrying with fallback term.")

    # Final fallback: press Enter on whatever is highlighted
    logger.info("[DM SENDER] ⚠️  All strategies failed — pressing Enter as last resort.")
    page.keyboard.press("Enter")
    _rand_delay(0.5, 1.0)

    pill = page.locator(
        ".msg-connections-typeahead__pills .msg-compose__pill, "
        "[class*='typeahead'] [class*='pill']"
    ).first
    if pill.count() > 0:
        logger.info(f"[DM SENDER] ✅ Recipient chip confirmed (Enter fallback): {pill.inner_text().strip()!r}")
        return True

    return False


def send_linkedin_dm(contact: LinkedinContact | LinkedinSearchContact, message: str, db: Session, profile_id: int = 1) -> bool:
    """
    Send a LinkedIn DM to a 1st-degree connection.

    Parameters
    ----------
    contact : LinkedinContact with is_connected=True and a valid profile_url.
    message : The DM body text (pitch_body from SalesPitch).

    Returns
    -------
    True if the message was sent successfully, False otherwise.
    Raises LinkedInSessionExpiredError if the LinkedIn session is invalid.
    """
    if not contact.profile_url:
        logger.info(f"[DM SENDER] ⚠️  No profile_url for contact {contact.name} — skipping.")
        return False

    if not contact.is_connected:
        logger.info(f"[DM SENDER] ⏭️  {contact.name} not yet connected — skipping.")
        return False

    logger.info(f"[DM SENDER] 📨 Sending DM to {contact.name} ({contact.profile_url})")

    profile = db.query(ProfileSetting).filter(ProfileSetting.id == profile_id).first()
    if not profile or not profile.session_state:
        logger.info(f"[DM SENDER] Profile {profile_id} not found or missing session state.")
        return False
    
    try:
        session_dict = json.loads(profile.session_state)
    except json.JSONDecodeError:
        logger.info(f"[DM SENDER] Profile {profile_id} session state is invalid JSON.")
        return False

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=HEADLESS,
            slow_mo=random.randint(450, 750),
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
            # 1. Verify session is still valid
            page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=30000)
            _rand_delay(2, 3)
            _check_session(page)
            _dismiss_feed_popups(page)   # close Premium / promo modal if present

            # 2. Navigate to the contact's profile
            page.goto(contact.profile_url, wait_until="domcontentloaded", timeout=30000)
            _rand_delay(2, 4)
            _check_session(page)

            # Extra settle — LinkedIn lazy-loads the action buttons after DOM ready
            page.wait_for_timeout(2500)

            # 3. Primary: extract the compose URL from the profile "Message" link.
            #    LinkedIn renders the Message button as an <a> tag whose href is:
            #    /messaging/compose/?profileUrn=...&recipient=...
            #    Navigating to this URL directly opens the compose page with the
            #    recipient already pre-filled — no typeahead, no To: field to fill.
            first_name = contact.name.strip().split()[0]

            compose_url = None
            link_selectors = [
                # Most specific: href contains both /messaging/compose and recipient
                "a[href*='/messaging/compose'][href*='recipient']",
                # Profile top-card message link by aria-label
                f"a[aria-label='Message {contact.name}'][href*='messaging']",
                f"a[aria-label*='Message'][aria-label*='{first_name}'][href*='messaging']",
                # Any message-related link in the top card
                "a[href*='/messaging/compose']",
            ]
            for sel in link_selectors:
                try:
                    lnk = page.locator(sel).first
                    if lnk.count() > 0:
                        href = lnk.get_attribute("href")
                        if href and "/messaging/" in href:
                            compose_url = href if href.startswith("http") else f"https://www.linkedin.com{href}"
                            logger.info(f"[DM SENDER] 🔗 Compose URL found via {sel!r}")
                            break
                except Exception:
                    continue

            used_direct_flow = False

            if compose_url:
                # Navigate directly to the pre-filled compose URL
                logger.info(f"[DM SENDER] 💬  Navigating directly to compose URL (recipient pre-filled)")
                page.goto(compose_url, wait_until="domcontentloaded", timeout=30000)
                _rand_delay(2.0, 3.0)
                used_direct_flow = True

            else:
                # Fallback: click the generic Message button + fill recipient manually
                logger.info(f"[DM SENDER] ⚠️  Compose URL not found — falling back to generic Message button.")
                message_btn = _find_message_button(page, contact.name)

                if message_btn is None:
                    logger.info(
                        f"[DM SENDER] ⚠️  No 'Message' button found for {contact.name}. "
                        "They may not be connected or have messaging restricted."
                    )
                    return False

                message_btn.scroll_into_view_if_needed()
                _rand_delay(0.5, 1.0)
                message_btn.click(force=True)
                _rand_delay(2.5, 3.5)

                job_title = getattr(contact, "job_title", getattr(contact, "position", "")) or ""
                if not _fill_recipient(page, contact.name, job_title=job_title):
                    logger.info(f"[DM SENDER] ⚠️  Could not fill recipient for {contact.name} — aborting.")
                    return False

                page.keyboard.press("Tab")
                _rand_delay(0.5, 0.8)

            # 4. Find the compose box (same selector works for both flows)
            compose_box = page.locator(
                "div.msg-form__contenteditable, "
                "div[contenteditable='true'][aria-label*='Write a message' i], "
                "div[contenteditable='true'][aria-label*='message' i], "
                "div[role='textbox'][contenteditable='true']"
            ).first

            try:
                compose_box.wait_for(state="visible", timeout=10000)
            except PlaywrightTimeout:
                logger.info(f"[DM SENDER] ⚠️  Compose box did not appear for {contact.name}.")
                return False

            compose_box.click(force=True)
            _rand_delay(0.5, 1.0)

            # 5. Type the message with human-like character delays
            _type_humanlike(page, message)
            _rand_delay(1.0, 2.0)

            # 6. Send
            send_btn = page.locator(
                "button.msg-form__send-button, "
                "button[aria-label='Send'], "
                "button[type='submit']:has-text('Send'), "
                "button:has-text('Send')"
            ).first

            if send_btn.count() == 0:
                logger.info(f"[DM SENDER] ⚠️  Send button not found for {contact.name}.")
                return False

            send_btn.click(force=True)
            _rand_delay(1.5, 2.5)

            flow = "direct" if used_direct_flow else "dialog"
            logger.info(f"[DM SENDER] ✅ DM sent to {contact.name} (flow={flow})")
            return True

        except LinkedInSessionExpiredError:
            logger.info("[DM SENDER] ⛔ LinkedIn session expired.")
            raise

        except Exception as exc:
            logger.info(f"[DM SENDER] ❌ Error sending DM to {contact.name}: {exc}")
            return False

        finally:
            try:
                profile.session_state = json.dumps(context.storage_state())
                db.commit()
            except Exception:
                pass
            browser.close()


def _type_humanlike(page, text: str) -> None:
    """Type text into the focused element with random per-character delays."""
    for char in text:
        page.keyboard.type(char)
        time.sleep(random.uniform(0.03, 0.09))
