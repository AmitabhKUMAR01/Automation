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

def _open_conversation(page, profile_url: str, contact_name: str) -> bool:
    # Navigate to profile
    page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
    _rand_delay(2, 3)
    _check_session(page)

    # Extra settle — LinkedIn lazy-loads action buttons
    page.wait_for_timeout(2000)

    first_name = contact_name.strip().split()[0]

    # ── Strategy 1: extract compose URL from profile Message link ──────────────
    compose_url = None
    link_selectors = [
        "a[href*='/messaging/compose'][href*='recipient']",
        f"a[aria-label='Message {contact_name}'][href*='messaging']",
        f"a[aria-label*='Message'][aria-label*='{first_name}'][href*='messaging']",
        "a[href*='/messaging/compose']",
    ]
    for sel in link_selectors:
        try:
            lnk = page.locator(sel).first
            if lnk.count() > 0:
                href = lnk.get_attribute("href")
                if href and "/messaging/" in href:
                    compose_url = (
                        href if href.startswith("http")
                        else f"https://www.linkedin.com{href}"
                    )
                    logger.info(f"[REPLY CHECKER] 🔗 Compose URL found via {sel!r}")
                    break
        except Exception:
            continue

    if compose_url:
        page.goto(compose_url, wait_until="domcontentloaded", timeout=30000)
        _rand_delay(2, 3)
        logger.info(f"[REPLY CHECKER] 💬  Navigated to conversation via compose URL")
        return True

    # ── Strategy 2: click the generic Message button ───────────────────────────
    btn_selectors = [
        f"button[aria-label*='Message'][aria-label*='{first_name}']",
        "button[aria-label='Message']",
        "button[aria-label*='Message']",
        "div.pvs-profile-actions button:has-text('Message')",
        "div.ph5 button:has-text('Message')",
        "button:has-text('Message')",
    ]
    for sel in btn_selectors:
        try:
            btn = page.locator(sel).first
            if btn.count() > 0 and btn.is_visible():
                btn.scroll_into_view_if_needed()
                _rand_delay(0.4, 0.8)
                btn.click(force=True)
                _rand_delay(2, 3)
                logger.info(f"[REPLY CHECKER] 💬  Clicked Message button via {sel!r}")
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

    # ── Scroll up slightly to load recent messages ────────────────────────────
    try:
        page.evaluate("document.querySelector('div.msg-s-message-list-container, ul.msg-s-message-list')?.scrollTo(0, 99999)")
        _rand_delay(1, 1.5)
    except Exception:
        pass

    # ── Extract each message bubble ───────────────────────────────────────────
    msg_items = page.locator(
        "li.msg-s-message-list__event, "
        "li[class*='msg-s-event-listitem'], "
        "div[class*='msg-s-event-listitem']"
    )

    count = msg_items.count()
    logger.info(f"[REPLY CHECKER] 📩  Found {count} message element(s) in thread")

    # ── Debug: dump first few items' classes to help tune selectors ──────────
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

            if "other" in classes:
                is_self = False
            elif "sent-by-me" in classes or "outgoing" in classes:
                is_self = True
            else:
                # Check aria-label on any nested time/status element
                aria_els = item.locator(
                    "[aria-label*='You sent'], "
                    "[aria-label*='Sent by you'], "
                    "[aria-label*='you sent']"
                )
                if aria_els.count() > 0:
                    is_self = True
                else:
                    # Check if the sender name element exists and matches the
                    # contact pattern (incoming messages show sender name)
                    sender_el = item.locator(
                        ".msg-s-event-listitem__sender-name, "
                        "[class*='sender-name'], "
                        "[class*='actor-name']"
                    )
                    if sender_el.count() > 0:
                        is_self = False  # incoming messages have a visible sender label
                    else:
                        is_self = True   # no sender label → outgoing (default safe)

            # Extract message body text
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

    if not profile_url:
        logger.info(f"[REPLY CHECKER] ⚠️  No profile_url — skipping {contact_name}")
        return result

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
            # 1. Verify session
            page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=30000)
            _rand_delay(1.5, 2.5)
            _check_session(page)

            # 2. Open the conversation thread
            opened = _open_conversation(page, profile_url, contact_name)
            if not opened:
                logger.info(f"[REPLY CHECKER] ⚠️  Could not open thread for {contact_name} — skipping.")
                return result

            # 3. Read messages
            messages = _read_thread_messages(page)

            if not messages:
                logger.info(f"[REPLY CHECKER] ℹ️   No messages found in thread (or couldn't parse).")
                return result

            # 4. Check: is the LAST message from the contact (not from us)?
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
