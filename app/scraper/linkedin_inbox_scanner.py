"""
linkedin_inbox_scanner.py

Phase 1 of the hybrid reply checker.
Navigates to linkedin.com/messaging/ and scans the conversation list
for unread indicators and recent activity. Returns a lightweight list
of conversations that likely have new replies, avoiding the need to
open each thread individually.
"""
from __future__ import annotations
from app.utils.logger import logger
import time
import re
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


MESSAGING_URL = "https://www.linkedin.com/messaging/"


def _extract_slug_from_href(href: str) -> str | None:
    """Extract the /in/<slug> portion from a LinkedIn URL."""
    if not href:
        return None
    match = re.search(r"/in/([^/?#]+)", href)
    if match:
        return match.group(1).lower().rstrip("/")
    return None


def _parse_conversation_cards(page, max_cards: int = 50) -> list[dict]:
    """
    Parse visible conversation cards from the LinkedIn messaging sidebar.
    Returns a list of dicts with conversation metadata.
    """
    conversations: list[dict] = []

    # ── Locate conversation list items ────────────────────────────────────────
    card_selectors = [
        "li.msg-conversation-listitem",
        "li[class*='msg-conversation-listitem']",
        "li[class*='msg-conversations-container__convo-item']",
        "div[class*='msg-conversation-listitem']",
    ]

    cards = None
    for sel in card_selectors:
        try:
            loc = page.locator(sel)
            if loc.count() > 0:
                cards = loc
                logger.info(f"[INBOX SCAN] 🔍 Found conversation cards via: {sel!r} (count={loc.count()})")
                break
        except Exception:
            continue

    if cards is None or cards.count() == 0:
        logger.info("[INBOX SCAN] ⚠️  No conversation cards found on messaging page.")
        return conversations

    count = min(cards.count(), max_cards)
    logger.info(f"[INBOX SCAN] 📋 Parsing {count} conversation card(s)…")

    for i in range(count):
        card = cards.nth(i)
        try:
            entry: dict = {
                "name": "",
                "profile_slug": None,
                "preview": "",
                "has_unread": False,
                "card_index": i,
            }

            # ── Check for unread indicator ────────────────────────────────────
            card_classes = card.get_attribute("class") or ""
            if "unread" in card_classes.lower():
                entry["has_unread"] = True

            # Also check for unread badge/dot elements inside the card
            if not entry["has_unread"]:
                unread_selectors = [
                    "[class*='msg-conversation-card--unread']",
                    "[class*='notification-badge']",
                    "[class*='unread']",
                    "span[class*='notification-badge']",
                    ".msg-conversation-card__unread-count",
                ]
                for usel in unread_selectors:
                    try:
                        unread_el = card.locator(usel)
                        if unread_el.count() > 0:
                            entry["has_unread"] = True
                            break
                    except Exception:
                        continue

            # Also check if the title text is bold (LinkedIn uses font-weight
            # for unread conversations)
            if not entry["has_unread"]:
                try:
                    title_el = card.locator(
                        "h3[class*='msg-conversation-card__title'], "
                        "[class*='msg-conversation-card__title']"
                    ).first
                    if title_el.count() > 0:
                        font_weight = title_el.evaluate(
                            "el => window.getComputedStyle(el).fontWeight"
                        )
                        # font-weight >= 600 typically means bold/semibold
                        if font_weight and int(font_weight) >= 600:
                            entry["has_unread"] = True
                except Exception:
                    pass

            # ── Extract contact name ──────────────────────────────────────────
            name_selectors = [
                "h3.msg-conversation-card__title",
                "h3[class*='msg-conversation-card__title']",
                "[class*='msg-conversation-card__title']",
                "h3[class*='msg-conversation-listitem__title']",
            ]
            for nsel in name_selectors:
                try:
                    name_el = card.locator(nsel).first
                    if name_el.count() > 0:
                        entry["name"] = name_el.inner_text().strip()
                        break
                except Exception:
                    continue

            # ── Extract profile slug from any /in/ link ───────────────────────
            try:
                links = card.locator("a[href*='/in/']")
                for li in range(links.count()):
                    href = links.nth(li).get_attribute("href") or ""
                    slug = _extract_slug_from_href(href)
                    if slug:
                        entry["profile_slug"] = slug
                        break
            except Exception:
                pass

            # ── Extract message preview snippet ───────────────────────────────
            preview_selectors = [
                "p.msg-conversation-card__message-snippet",
                "p[class*='msg-conversation-card__message-snippet']",
                "[class*='message-snippet']",
                "p.msg-conversation-card__message-snippet-body",
                "[class*='msg-conversation-listitem__message-snippet']",
            ]
            for psel in preview_selectors:
                try:
                    preview_el = card.locator(psel).first
                    if preview_el.count() > 0:
                        entry["preview"] = preview_el.inner_text().strip()[:200]
                        break
                except Exception:
                    continue

            # Only include cards with a name
            if entry["name"]:
                conversations.append(entry)

        except Exception as exc:
            logger.info(f"[INBOX SCAN] ⚠️  Error parsing card {i}: {exc}")
            continue

    return conversations


def scan_inbox(
    db: Session,
    profile_id: int = 1,
    max_scroll: int = 3,
) -> dict:
    """
    Open the LinkedIn Messaging inbox and scan for conversations
    with unread indicators.

    Returns
    -------
    {
        "success": bool,
        "conversations": [
            {"name": str, "profile_slug": str|None, "preview": str, "has_unread": bool},
            ...
        ],
        "unread_conversations": [<subset with has_unread=True>],
        "total_scanned": int,
        "error": str|None,
    }
    """
    result = {
        "success": False,
        "conversations": [],
        "unread_conversations": [],
        "total_scanned": 0,
        "error": None,
    }

    profile = db.query(ProfileSetting).filter(ProfileSetting.id == profile_id).first()
    if not profile or not profile.session_state:
        logger.info(f"[INBOX SCAN] Profile {profile_id} not found or missing session state.")
        result["error"] = "profile_invalid"
        return result

    try:
        session_dict = json.loads(profile.session_state)
    except json.JSONDecodeError:
        logger.info(f"[INBOX SCAN] Profile {profile_id} session state is invalid JSON.")
        result["error"] = "session_invalid"
        return result

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=HEADLESS,
            slow_mo=400,
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
            # 1. Navigate to messaging inbox
            logger.info("[INBOX SCAN] 📬 Navigating to LinkedIn Messaging inbox…")
            page.goto(MESSAGING_URL, wait_until="domcontentloaded", timeout=30000)
            _rand_delay(3, 5)
            _check_session(page)

            # 2. Wait for conversation list to load
            list_selectors = [
                "ul.msg-conversations-container__conversations-list",
                "ul[class*='msg-conversations-container']",
                "div.msg-conversations-container__conversations-list",
                "li.msg-conversation-listitem",
            ]
            list_loaded = False
            for sel in list_selectors:
                try:
                    page.wait_for_selector(sel, timeout=10000)
                    list_loaded = True
                    logger.info(f"[INBOX SCAN] ✅ Conversation list loaded via: {sel!r}")
                    break
                except PlaywrightTimeout:
                    continue

            if not list_loaded:
                logger.info("[INBOX SCAN] ⚠️  Conversation list did not load — aborting.")
                result["error"] = "conversation_list_not_loaded"
                return result

            # 3. Scroll down to load more conversations
            for scroll_i in range(max_scroll):
                try:
                    # Scroll the conversation sidebar, not the main page
                    page.evaluate("""
                        const container = document.querySelector(
                            'ul.msg-conversations-container__conversations-list, '  +
                            'div.msg-conversations-container__conversations-list, ' +
                            'ul[class*="msg-conversations-container"]'
                        );
                        if (container) {
                            container.scrollTo(0, container.scrollHeight);
                        } else {
                            window.scrollTo(0, document.body.scrollHeight);
                        }
                    """)
                    _rand_delay(1.5, 2.5)
                except Exception:
                    pass

            # 4. Parse conversation cards
            conversations = _parse_conversation_cards(page)
            result["conversations"] = conversations
            result["total_scanned"] = len(conversations)
            result["unread_conversations"] = [c for c in conversations if c["has_unread"]]
            result["success"] = True

            logger.info(
                f"[INBOX SCAN] 📊 Scan complete — "
                f"total: {result['total_scanned']} | "
                f"unread: {len(result['unread_conversations'])}"
            )

            # Log unread conversations for debugging
            for conv in result["unread_conversations"]:
                logger.info(
                    f"[INBOX SCAN] 📩 Unread: {conv['name']} "
                    f"(slug={conv['profile_slug']}) — "
                    f"preview: {conv['preview'][:60]!r}"
                )

        except LinkedInSessionExpiredError:
            logger.info("[INBOX SCAN] ⛔ LinkedIn session expired.")
            result["error"] = "session_expired"
            raise

        except Exception as exc:
            logger.info(f"[INBOX SCAN] ❌ Error scanning inbox: {exc}")
            result["error"] = str(exc)

        finally:
            # Persist updated session cookies
            try:
                profile.session_state = json.dumps(context.storage_state())
                db.commit()
            except Exception:
                pass
            browser.close()

    return result
