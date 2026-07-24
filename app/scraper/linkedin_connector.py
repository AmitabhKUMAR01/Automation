from app.utils.logger import logger
import os
import time
import random
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from sqlalchemy.orm import Session
from app.models.linkedin_contact import LinkedinContact
from app.models.linkedin_search_contact import LinkedinSearchContact
from app.models.sales_pitch import SalesPitch
import app.models.lead_score
import app.models.business_client
from app.services.sales_pitch_service import generate_sales_pitch
import uuid
from app.scraper.linkedin_finder import (
    HEADLESS,
    LinkedInSessionExpiredError,
    _rand_delay,
    _check_session,
)
import json
from app.models.profile_setting import ProfileSetting


def send_connection_requests(
    business_client_id: int,
    db: Session,
    limit: int = 5,
    message: str | None = None,
    profile_id: int = 1,
) -> dict:
    result = {"attempted": 0, "sent": 0, "failed": 0, "session_expired": False}

    # ── Query pending contacts, highest confidence first ───────────────────────
    confidence_order = {
        "high":   0,
        "medium": 1,
        "low":    2,
    }
    contacts = (
        db.query(LinkedinContact)
        .filter(
            LinkedinContact.business_client_id == business_client_id,
            LinkedinContact.connection_sent    == False,  # noqa: E712
        )
        .all()
    )

    # Sort by confidence in Python (avoids DB-specific CASE syntax)
    contacts.sort(key=lambda c: confidence_order.get(c.match_confidence or "low", 2))
    contacts = contacts[:limit]

    if not contacts:
        logger.info(f"[CONNECTOR] No pending contacts for client_id={business_client_id}")
        return result

    logger.info(f"\n[CONNECTOR] 📨 Sending connections to {len(contacts)} contact(s) for client_id={business_client_id}")

    profile = db.query(ProfileSetting).filter(ProfileSetting.id == profile_id).first()
    if not profile or not profile.session_state:
        logger.info(f"[CONNECTOR] Profile {profile_id} not found or missing session state.")
        result["error"] = "profile_invalid"
        return result
    
    try:
        session_dict = json.loads(profile.session_state)
    except json.JSONDecodeError:
        logger.info(f"[CONNECTOR] Profile {profile_id} session state is invalid JSON.")
        result["error"] = "session_invalid"
        return result

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=HEADLESS,
            slow_mo=random.randint(600, 1000),
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
            # Verify session first
            page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=30000)
            _rand_delay(2, 3)
            _check_session(page)
            logger.info("[CONNECTOR] ✅ Session valid")

            for contact in contacts:
                result["attempted"] += 1
                logger.info(f"\n[CONNECTOR] → Visiting: {contact.name} | {contact.profile_url}")

                try:
                    page.goto(contact.profile_url, wait_until="domcontentloaded", timeout=30000)
                    _rand_delay(2, 3)

                    # Check session on every profile visit
                    _check_session(page)

                    sent = _try_connect(page, message)

                    if sent:
                        contact.connection_sent    = True
                        contact.connection_sent_at = datetime.now(timezone.utc)
                        db.commit()
                        result["sent"] += 1
                        logger.info(f"[CONNECTOR] ✅ Connection sent to {contact.name}")
                    else:
                        result["failed"] += 1
                        logger.info(f"[CONNECTOR] ⚠️  Could not send connection to {contact.name}")

                    # Human-like delay between connections (longer to avoid rate-limit)
                    _rand_delay(3, 6)

                except LinkedInSessionExpiredError:
                    result["session_expired"] = True
                    logger.info("[CONNECTOR] ⚠️  Session expired mid-run — stopping batch.")
                    raise   # stop outer loop via finally

                except Exception as err:
                    result["failed"] += 1
                    logger.info(f"[CONNECTOR] ❌ Error for {contact.name}: {err}")
                    _rand_delay(2, 4)
                    continue

        except LinkedInSessionExpiredError:
            result["session_expired"] = True

        finally:
            try:
                profile.session_state = json.dumps(context.storage_state())
                db.commit()
            except Exception:
                pass
            browser.close()

    logger.info(f"[CONNECTOR] Done — attempted={result['attempted']}, sent={result['sent']}, failed={result['failed']}")
    return result


def send_daily_global_connections(
    db: Session,
    limit: int = 5,
    message: str | None = None,
    profile_id: int = 1,
) -> dict:
    """
    Send LinkedIn connection requests to unsent contacts across ALL business clients,
    up to a daily limit. This helps prevent account rate-limits/bans.
    Processes contacts ordered by match_confidence (high → medium → low).
    """
    result = {"attempted": 0, "sent": 0, "failed": 0, "session_expired": False}

    confidence_order = {
        "high":   0,
        "medium": 1,
        "low":    2,
    }
    
    # Get unsent contacts across all clients
    contacts = (
        db.query(LinkedinContact)
        .filter(LinkedinContact.connection_sent == False)  # noqa: E712
        .all()
    )

    # Sort by confidence in Python and limit
    contacts.sort(key=lambda c: confidence_order.get(c.match_confidence or "low", 2))
    contacts = contacts[:limit]

    if not contacts:
        logger.info(f"[CONNECTOR BATCH] No pending contacts to send connection requests to.")
        return result

    logger.info(f"\n[CONNECTOR BATCH] 📨 Sending global daily connections to {len(contacts)} contact(s) (limit={limit})")

    profile = db.query(ProfileSetting).filter(ProfileSetting.id == profile_id).first()
    if not profile or not profile.session_state:
        logger.info(f"[CONNECTOR BATCH] Profile {profile_id} not found or missing session state.")
        result["error"] = "profile_invalid"
        return result
    
    try:
        session_dict = json.loads(profile.session_state)
    except json.JSONDecodeError:
        logger.info(f"[CONNECTOR BATCH] Profile {profile_id} session state is invalid JSON.")
        result["error"] = "session_invalid"
        return result

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=HEADLESS,
            slow_mo=random.randint(600, 1000),
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
            # Verify session first
            page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=30000)
            _rand_delay(2, 3)
            _check_session(page)
            logger.info("[CONNECTOR BATCH] ✅ Session valid")

            for contact in contacts:
                result["attempted"] += 1
                logger.info(f"\n[CONNECTOR BATCH] → Visiting: {contact.name} | {contact.profile_url}")

                try:
                    page.goto(contact.profile_url, wait_until="domcontentloaded", timeout=30000)
                    _rand_delay(2, 3)

                    # Check session on every profile visit
                    _check_session(page)

                    sent = _try_connect(page, message)

                    if sent:
                        contact.connection_sent = True
                        contact.connection_sent_at = datetime.now(timezone.utc)
                        db.commit()
                        result["sent"] += 1
                        logger.info(f"[CONNECTOR BATCH] ✅ Connection sent to {contact.name}")
                    else:
                        result["failed"] += 1
                        logger.info(f"[CONNECTOR BATCH] ⚠️  Could not send connection to {contact.name}")

                    # Human-like delay between connections
                    _rand_delay(3, 6)

                except LinkedInSessionExpiredError:
                    result["session_expired"] = True
                    logger.info("[CONNECTOR BATCH] ⚠️  Session expired mid-run — stopping batch.")
                    raise

                except Exception as err:
                    result["failed"] += 1
                    logger.info(f"[CONNECTOR BATCH] ❌ Error for {contact.name}: {err}")
                    _rand_delay(2, 4)
                    continue

        except LinkedInSessionExpiredError:
            result["session_expired"] = True

        finally:
            try:
                profile.session_state = json.dumps(context.storage_state())
                db.commit()
            except Exception:
                pass
            browser.close()

    logger.info(f"[CONNECTOR BATCH] Done — attempted={result['attempted']}, sent={result['sent']}, failed={result['failed']}")
    return result


def _try_connect(page, message: str | None) -> bool:
    """
    Attempt to click the Connect button on a profile page and optionally add a note.
    Returns True if the connection request was successfully dispatched.
    """
    try:
        ## Scope all search to the profile top card to avoid clicking sidebar buttons
        top_card = page.locator("main section").first

        # Strategy 1: Direct "Connect" button in the profile actions
        connect_btn = top_card.locator(
            "button[aria-label*='Connect'], "
            "a[aria-label*='Connect'], "
            "button[aria-label*='Invite'], "
            "a[aria-label*='Invite']"
        ).first

        if connect_btn.count() == 0:
            # Strategy 2: "More" dropdown → "Connect"
            more_btn = top_card.locator(
                "button[aria-label='More'], "
                "button[aria-label*='More actions'], "
                "button:has-text('More')"
            ).first
            if more_btn.count() == 0:
                return False
            more_btn.click(force=True)
            _rand_delay(0.5, 1)
            connect_btn = page.locator(
                "[role='menuitem']"
            ).filter(has_text="Connect").first

        if connect_btn.count() == 0:
            # Dismiss dropdown if open
            page.keyboard.press("Escape")
            return False

        connect_btn.click(force=True)
        _rand_delay(1, 2)

        # ── Send dialog appears ──────────────────────────────────────────────
        if message:
            # Click "Add a note" if available
            add_note_btn = page.locator(
                "button[aria-label*='Add a note'], "
                "button:has-text('Add a note')"
            ).first
            if add_note_btn.count() > 0:
                add_note_btn.click()
                _rand_delay(0.5, 1)
                # Type the message into the textarea
                note_area = page.locator("textarea[name='message']").first
                if note_area.count() > 0:
                    note_area.fill(message)
                    _rand_delay(0.5, 1)

        # Click Send / Send without a note
        send_btn = page.locator(
            "button[aria-label='Send now'], "
            "button:has-text('Send without a note'), "
            "button:has-text('Send')"
        ).first

        if send_btn.count() == 0:
            # Try to dismiss if something went wrong
            page.keyboard.press("Escape")
            return False

        send_btn.click(force=True)
        _rand_delay(1, 2)
        return True

    except PlaywrightTimeout:
        return False
    except Exception:
        return False


def check_recent_connections(db: Session, max_scroll: int = 3, profile_id: int = 1) -> dict:
    """
    Visit the LinkedIn 'My Connections' page and detect which of our
    previously sent connection requests have now been accepted.

    Strategy:
      - Load https://www.linkedin.com/mynetwork/invite-connect/connections/
      - Extract profile slugs from every connection card link (href contains /in/)
      - Cross-reference against our database records where connection_sent=True
        and is_connected=False
      - Mark matches as is_connected=True with the current timestamp

    Returns: { "checked": N, "newly_accepted": N, "names": [...] }
    """
    result: dict = {"checked": 0, "newly_accepted": 0, "names": []}

    CONNECTIONS_URL = "https://www.linkedin.com/mynetwork/invite-connect/connections/"

    profile = db.query(ProfileSetting).filter(ProfileSetting.id == profile_id).first()
    if not profile or not profile.session_state:
        logger.info(f"[ACCEPTANCE CHECK] Profile {profile_id} not found or missing session state.")
        result["error"] = "profile_invalid"
        return result
    
    try:
        session_dict = json.loads(profile.session_state)
    except json.JSONDecodeError:
        logger.info(f"[ACCEPTANCE CHECK] Profile {profile_id} session state is invalid JSON.")
        result["error"] = "session_invalid"
        return result

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=HEADLESS,
            slow_mo=random.randint(600, 1000),
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
            page.goto(CONNECTIONS_URL, wait_until="domcontentloaded", timeout=60000)
            _rand_delay(3, 5)
            _check_session(page)

            # Scroll down to load more connections
            for _ in range(max_scroll):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                _rand_delay(1.5, 2.5)

            # Extract all profile URLs from the connections page
            links = page.locator("a[href*='/in/']")
            accepted_slugs: set[str] = set()
            for i in range(links.count()):
                href = links.nth(i).get_attribute("href") or ""
                # Normalise: extract the /in/<slug> part only
                if "/in/" in href:
                    # Strip query params and trailing slashes
                    slug = href.split("/in/")[1].split("?")[0].rstrip("/")
                    if slug:
                        accepted_slugs.add(slug.lower())

            logger.info(f"[ACCEPTANCE CHECK] Found {len(accepted_slugs)} profiles on connections page.")

            if not accepted_slugs:
                return result

            # Load all contacts that we sent but haven't yet marked as connected
            pending_contacts = (
                db.query(LinkedinContact)
                .filter(
                    LinkedinContact.connection_sent == True,   # noqa: E712
                    LinkedinContact.is_connected   == False,  # noqa: E712
                )
                .all()
            )

            pending_search = (
                db.query(LinkedinSearchContact)
                .filter(
                    LinkedinSearchContact.connection_sent == True,   # noqa: E712
                    LinkedinSearchContact.is_connected   == False,  # noqa: E712
                )
                .all()
            )

            result["checked"] = len(pending_contacts) + len(pending_search)
            logger.info(
                f"[ACCEPTANCE CHECK] Checking {len(pending_contacts)} standard & "
                f"{len(pending_search)} search contact(s) for acceptance…"
            )

            # 1. Process regular contacts
            for contact in pending_contacts:
                if not contact.profile_url:
                    continue
                if "/in/" not in contact.profile_url:
                    continue
                contact_slug = contact.profile_url.split("/in/")[1].split("?")[0].rstrip("/").lower()

                if contact_slug in accepted_slugs:
                    contact.is_connected = True
                    contact.connected_at  = datetime.now(timezone.utc)
                    db.commit()
                    result["newly_accepted"] += 1
                    result["names"].append(contact.name)
                    logger.info(f"[ACCEPTANCE CHECK] 🎉 {contact.name} accepted your connection!")

            # 2. Process search contacts
            for contact in pending_search:
                if not contact.profile_url:
                    continue
                if "/in/" not in contact.profile_url:
                    continue
                contact_slug = contact.profile_url.split("/in/")[1].split("?")[0].rstrip("/").lower()

                if contact_slug in accepted_slugs:
                    contact.is_connected = True
                    contact.connected_at  = datetime.now(timezone.utc)
                    db.commit()

                    # Automatically generate generic SalesPitch
                    already_pitched = db.query(SalesPitch).filter(
                        SalesPitch.linkedin_search_contact_id == contact.id
                    ).first()

                    if not already_pitched:
                        pitch_data = generate_sales_pitch(
                            client=None,
                            audit=None,
                            score_data=None,
                            linkedin_contact=None,
                            profile_id=profile_id,
                        )
                        db.add(SalesPitch(
                            uuid=str(uuid.uuid4()),
                            business_client_id=None,
                            lead_score_id=None,
                            linkedin_contact_id=None,
                            linkedin_search_contact_id=contact.id,
                            pitch_subject=pitch_data.get("pitch_subject"),
                            pitch_hook=pitch_data.get("pitch_hook"),
                            pitch_body=pitch_data.get("pitch_body"),
                            key_pain_points=pitch_data.get("key_pain_points"),
                            generated_by=pitch_data.get("generated_by"),
                            pitch_channel="linkedin",
                            generated_at=datetime.now(timezone.utc),
                            delivery_status="pending",
                        ))
                        db.commit()
                        logger.info(f"[ACCEPTANCE CHECK] 💬 Generated generic pitch for search contact {contact.name}")

                    result["newly_accepted"] += 1
                    result["names"].append(contact.name)
                    logger.info(f"[ACCEPTANCE CHECK] 🎉 Search contact {contact.name} accepted your connection!")

            if result["newly_accepted"] == 0:
                logger.info("[ACCEPTANCE CHECK] No new acceptances detected.")

        except LinkedInSessionExpiredError:
            logger.info("[ACCEPTANCE CHECK] ⚠️  Session expired — skipping check.")

        except Exception as err:
            logger.info(f"[ACCEPTANCE CHECK] ❌ Error: {err}")

        finally:
            try:
                profile.session_state = json.dumps(context.storage_state())
                db.commit()
            except Exception:
                pass
            browser.close()

    return result
