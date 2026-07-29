from __future__ import annotations
from app.utils.logger import logger
import time
import random
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from app.scraper.linkedin_finder import (
    HEADLESS,
    LinkedInSessionExpiredError,
    _rand_delay,
    _check_session,
)
import json
from app.models.profile_setting import ProfileSetting
from sqlalchemy.orm import Session


# ── Helpers ────────────────────────────────────────────────────────────────────

MESSAGING_URL = "https://www.linkedin.com/messaging/"


def _open_conversation_via_inbox(page, contact_name: str) -> bool:
    """
    Navigate to the LinkedIn messaging inbox and open the conversation for
    contact_name by clicking their conversation card directly.

    This avoids visiting the contact's profile page, which would register
    a visible profile view on their end.

    Returns True if the thread was successfully opened.
    """
    logger.info(f"[REPLY CHECKER] 📬 Opening conversation via inbox (no profile visit) for: {contact_name!r}")
    page.goto(MESSAGING_URL, wait_until="domcontentloaded", timeout=30000)
    _rand_delay(2, 3)
    _check_session(page)

    # Wait for conversation list
    try:
        page.wait_for_selector(
            "ul.msg-conversations-container__conversations-list, "
            "li.msg-conversation-listitem",
            timeout=10000,
        )
    except PlaywrightTimeout:
        logger.info("[REPLY CHECKER] ⚠️  Messaging inbox did not load.")
        return False

    _rand_delay(1, 1.5)

    contact_name_lower = contact_name.strip().lower()

    import re as _re
    def _strip_punct(s: str) -> str:
        return _re.sub(r"[^\w\s]", "", s).strip()

    contact_clean = _strip_punct(contact_name_lower)
    first_token = contact_clean.split()[0] if contact_clean.split() else ""

    found_card = None
    matched_name = None

    # Walk and scroll incrementally up to 15 times to handle virtual sidebar list
    for scroll_step in range(15):
        card_selectors = [
            "li.msg-conversation-listitem",
            "li[class*='msg-conversation-listitem']",
        ]
        cards = None
        for sel in card_selectors:
            loc = page.locator(sel)
            if loc.count() > 0:
                cards = loc
                break

        if not cards or cards.count() == 0:
            # Maybe list is loading, scroll to trigger it
            pass
        else:
            count = cards.count()
            step_match = None
            for i in range(count):
                card = cards.nth(i)
                try:
                    name_el = card.locator(
                        "h3.msg-conversation-card__title, "
                        "h3[class*='msg-conversation-card__title'], "
                        "[class*='msg-conversation-card__title']"
                    ).first
                    if name_el.count() == 0:
                        continue
                    raw = name_el.inner_text().strip()
                    card_name = next((ln.strip() for ln in raw.splitlines() if ln.strip()), raw)
                    card_name_lower = card_name.strip().lower()
                    card_clean = _strip_punct(card_name_lower)

                    # Match by containment or first-name for abbreviated names
                    matched = (
                        contact_name_lower in card_name_lower
                        or card_name_lower in contact_name_lower
                        or (contact_clean and (contact_clean in card_clean or card_clean in contact_clean))
                        or (len(first_token) >= 4 and first_token in card_clean)
                    )
                    if matched:
                        # Double check if last token of DB name is abbreviated
                        tokens_orig = contact_name_lower.split()
                        last_token = tokens_orig[-1] if tokens_orig else ""
                        is_abbreviated = len(_strip_punct(last_token)) <= 1 or last_token.endswith(".")
                        
                        # If first token matched but it wasn't abbreviated, make sure it's a solid match
                        if not (contact_name_lower in card_name_lower or card_name_lower in contact_name_lower or (contact_clean and (contact_clean in card_clean or card_clean in contact_clean))):
                            if not is_abbreviated:
                                continue # Skip false positives on first name only
                        
                        step_match = (card, card_name)
                        break
                except Exception:
                    continue

            if step_match:
                found_card, matched_name = step_match
                break

        # Scroll down by a portion to load more cards
        try:
            page.evaluate("""
                const container = document.querySelector(
                    'ul.msg-conversations-container__conversations-list, '  +
                    'div.msg-conversations-container__conversations-list, ' +
                    'ul[class*="msg-conversations-container"]'
                );
                if (container) {
                    container.scrollBy(0, 400);
                } else {
                    window.scrollBy(0, 400);
                }
            """)
            _rand_delay(1.5, 2.0)
        except Exception:
            break

    if found_card:
        try:
            found_card.click()
            _rand_delay(2, 3)
            logger.info(f"[REPLY CHECKER] ✅ Opened conversation card for {matched_name!r} (matched {contact_name!r})")
            return "messaging" in page.url
        except Exception as exc:
            logger.info(f"[REPLY CHECKER] ⚠️  Failed to click matched card: {exc}")

    logger.info(f"[REPLY CHECKER] ⚠️  Conversation for {contact_name!r} not found in inbox — falling back to profile visit.")
    return False


def _open_conversation_via_profile(page, profile_url: str, contact_name: str) -> bool:
    """
    Fallback: navigate to the contact's profile page and use the Message
    button/link to open the thread. This DOES register a profile view.
    Only used when the inbox approach fails.
    """
    logger.info(f"[REPLY CHECKER] 🔗 Fallback — visiting profile page for {contact_name!r}")
    page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
    _rand_delay(2, 3)
    _check_session(page)
    page.wait_for_timeout(2000)

    first_name = contact_name.strip().split()[0]

    # Try compose URL link first
    for sel in (
        "a[href*='/messaging/compose'][href*='recipient']",
        f"a[aria-label='Message {contact_name}'][href*='messaging']",
        f"a[aria-label*='Message'][aria-label*='{first_name}'][href*='messaging']",
        "a[href*='/messaging/compose']",
    ):
        try:
            lnk = page.locator(sel).first
            if lnk.count() > 0:
                href = lnk.get_attribute("href")
                if href and "/messaging/" in href:
                    compose_url = href if href.startswith("http") else f"https://www.linkedin.com{href}"
                    logger.info(f"[REPLY CHECKER] 🔗 Compose URL found via {sel!r}")
                    page.goto(compose_url, wait_until="domcontentloaded", timeout=30000)
                    _rand_delay(2, 3)
                    return True
        except Exception:
            continue

    # Fallback: click Message button
    for sel in (
        f"button[aria-label*='Message'][aria-label*='{first_name}']",
        "button[aria-label='Message']",
        "div.pvs-profile-actions button:has-text('Message')",
        "button:has-text('Message')",
    ):
        try:
            btn = page.locator(sel).first
            if btn.count() > 0 and btn.is_visible():
                btn.scroll_into_view_if_needed()
                _rand_delay(0.4, 0.8)
                btn.click(force=True)
                _rand_delay(2, 3)
                return "linkedin.com/messaging" in page.url
        except Exception:
            continue

    logger.info(f"[REPLY CHECKER] ⚠️  Could not open conversation for {contact_name}")
    return False


def _read_thread_messages(page) -> list[dict]:
    _rand_delay(1.5, 2.5)

    messages = []

    # ── Wait for the thread container ─────────────────────────────────────────
    thread_selectors = [
        "div.msg-s-message-list-container",
        "div[class*='msg-s-message-list']",
        "ul.msg-s-message-list",
        "div[data-test-msg-conversation-listitem]",
    ]
    thread_loaded = False
    for sel in thread_selectors:
        try:
            page.wait_for_selector(sel, timeout=8000)
            thread_loaded = True
            break
        except PlaywrightTimeout:
            continue

    if not thread_loaded:
        logger.info("[REPLY CHECKER] ⚠️  Thread container not found.")
        return messages

    # ── Scroll to bottom to load the most recent messages ────────────────────
    try:
        page.evaluate("document.querySelector('div.msg-s-message-list-container, ul.msg-s-message-list')?.scrollTo(0, 99999)")
        _rand_delay(1, 1.5)
    except Exception:
        pass

    # ── Extract each message item ─────────────────────────────────────────────
    # Use ONLY <li> selectors — the old `div[class*='msg-s-event-listitem']`
    # also matched child bubble <div>s inside each <li>, producing duplicate
    # entries and causing the real last message to be misidentified.
    msg_items = page.locator(
        "li.msg-s-message-list__event, "
        "li[class*='msg-s-event-listitem']"
    )

    count = msg_items.count()
    logger.info(f"[REPLY CHECKER] 📩  Found {count} message element(s) in thread")

    # ── Debug: dump first 3 items' classes ───────────────────────────────────
    for dbg_i in range(min(3, count)):
        try:
            dbg_cls = msg_items.nth(dbg_i).get_attribute("class") or ""
            logger.info(f"[REPLY CHECKER] 🔎  item[{dbg_i}] classes: {dbg_cls[:120]!r}")
        except Exception:
            pass

    for i in range(count):
        item = msg_items.nth(i)
        try:
            classes = item.get_attribute("class") or ""

            # ── Check if this item is a real message event ────────────────
            # Time headings and other separators do not have msg-s-event-listitem.
            is_message = (
                "msg-s-event-listitem" in classes
                or item.locator("[class*='msg-s-event-listitem']").count() > 0
            )
            if not is_message:
                continue

            # ── DOM-verified sender detection ─────────────────────────────
            #
            # Confirmed via live DOM inspection (2026-07-29):
            #   INCOMING → inner div class contains "msg-s-event-listitem--other"
            #   OUTGOING → inner div class does NOT contain "--other"
            #
            # LinkedIn uses flexbox alignment (not `left` offset) so
            # getBoundingClientRect() returns the same values for both
            # sent and received — the position heuristic is unreliable.
            #
            # There is NO positive CSS marker for outgoing messages.
            # Absence of "--other" reliably means it was sent by us.
            #
            # Selector targets the inner child div specifically to avoid
            # matching unrelated descendants (e.g. seen-receipts divs).

            has_other = item.locator(
                "div.msg-s-event-listitem--other, "
                "div[class*='msg-s-event-listitem--other']"
            ).count() > 0

            if has_other:
                # Incoming message from the contact
                is_self = False
            else:
                # No "--other" marker → outgoing message sent by us.
                # Secondary checks below override only if they fire positively
                # (e.g. aria-labels added by future LinkedIn updates).
                is_self = True

                # Optional: aria-label double-check (future-proofing)
                aria_sent = item.locator(
                    "[aria-label*='You sent'], "
                    "[aria-label*='Sent by you'], "
                    "[aria-label*='you sent']"
                )
                # If aria says "You sent" it confirms is_self=True (already set).
                # If a sender-name element exists, it suggests incoming — flip.
                sender_el = item.locator(
                    ".msg-s-event-listitem__sender-name, "
                    "[class*='sender-name'], "
                    "[class*='actor-name']"
                )
                if sender_el.count() > 0 and aria_sent.count() == 0:
                    # Sender name present but no "You sent" aria → likely incoming
                    # that LinkedIn didn't mark with --other (rare edge case)
                    is_self = False

            # ── Extract message body text ─────────────────────────────────
            body_el = item.locator(
                "p.msg-s-event-listitem__body, "
                "span.msg-s-event-listitem__body, "
                "[class*='msg-s-event-listitem__body'], "
                "p[dir='ltr'], "
                "span[dir='ltr']"
            ).first

            body = ""
            if body_el.count() > 0:
                body = body_el.inner_text().strip()

            if not body:
                body = item.inner_text().strip()

            if body:
                sender = "You" if is_self else "Them"
                messages.append({"sender": sender, "body": body, "is_self": is_self})
                if i >= count - 3:  # log last 3 messages for debugging
                    logger.info(f"[REPLY CHECKER] 📨  msg[{i}] sender={sender!r}: {body[:60]!r}")

        except Exception as e:
            logger.info(f"[REPLY CHECKER] ⚠️  Error reading message {i}: {e}")
            continue

    return messages


# ── Public entry point ────────────────────────────────────────────────────────

def check_reply_for_contact(
    profile_url: str,
    contact_name: str,
    db: Session,
    delivered_at: datetime | None = None,
    profile_id: int = 1,
) -> dict:
    result = {"replied": False, "reply_text": None, "checked": True}

    logger.info(f"\n[REPLY CHECKER] 🔍 Checking replies for: {contact_name} ({profile_url})")

    profile = db.query(ProfileSetting).filter(ProfileSetting.id == profile_id).first()
    if not profile or not profile.session_state:
        logger.info(f"[REPLY CHECKER] Profile {profile_id} not found or missing session state.")
        return result

    try:
        session_dict = json.loads(profile.session_state)
    except json.JSONDecodeError:
        logger.info(f"[REPLY CHECKER] Profile {profile_id} session state is invalid JSON.")
        return result

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=HEADLESS,
            slow_mo=random.randint(400, 650),
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
            # 1. Open the conversation thread via inbox (no profile view)
            #    Falls back to profile visit only if the contact isn't visible in the inbox.
            opened = _open_conversation_via_inbox(page, contact_name)
            if not opened:
                logger.info(
                    f"[REPLY CHECKER] ℹ️  Not found in inbox — falling back to profile visit for {contact_name}"
                )
                if profile_url:
                    opened = _open_conversation_via_profile(page, profile_url, contact_name)

            if not opened:
                logger.info(f"[REPLY CHECKER] ⚠️  Could not open thread for {contact_name} — skipping.")
                return result

            # 2. Read messages
            messages = _read_thread_messages(page)

            if not messages:
                logger.info(f"[REPLY CHECKER] ℹ️   No messages found in thread (or couldn't parse).")
                return result

            # 3. Check: is the LAST message from the contact (not from us)?
            last_msg = messages[-1]
            if not last_msg["is_self"]:
                result["replied"]    = True
                result["reply_text"] = last_msg["body"][:2000]
                logger.info(
                    f"[REPLY CHECKER] 🎉 Reply detected from {contact_name}! "
                    f"Preview: {last_msg['body'][:80]!r}"
                )
            else:
                logger.info(f"[REPLY CHECKER] ℹ️   Last message is ours — no reply yet from {contact_name}.")

        except LinkedInSessionExpiredError:
            logger.info("[REPLY CHECKER] ⛔ LinkedIn session expired.")
            raise

        except Exception as exc:
            logger.info(f"[REPLY CHECKER] ❌ Error checking {contact_name}: {exc}")

        finally:
            try:
                profile.session_state = json.dumps(context.storage_state())
                db.commit()
            except Exception:
                pass
            browser.close()

    return result
