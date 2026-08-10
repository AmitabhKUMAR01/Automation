from __future__ import annotations
import re
import os
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.utils.logger import logger
from app.models.sales_pitch import SalesPitch
from app.models.linkedin_search_contact import LinkedinSearchContact
from app.models.linkedin_contact import LinkedinContact
from app.models.client_chat_message import ClientChatMessage
from app.scraper.linkedin_reply_checker import check_reply_for_contact
from app.scraper.linkedin_finder import LinkedInSessionExpiredError
from app.services.notification_service import notify_reply_received


# ── Slug helpers ───────────────────────────────────────────────────────────────

def _extract_slug(profile_url: str | None) -> str | None:
    if not profile_url:
        return None
    m = re.search(r"/in/([^/?#]+)", profile_url)
    return m.group(1).lower().rstrip("/") if m else None


def _strip_punct(s: str) -> str:
    return re.sub(r"[^\w\s]", "", s).strip()


def _name_matches(contact_name: str, inbox_names: set[str]) -> bool:
    """
    Multi-tier name match (mirrors the logic in pitch_reply_service).
    Returns True if contact_name is considered to match any inbox name.
    """
    contact_lower = contact_name.strip().lower()
    contact_clean = _strip_punct(contact_lower)

    for inbox_name in inbox_names:
        inbox_clean = _strip_punct(inbox_name)

        # Tier 1: direct containment
        if contact_lower in inbox_name or inbox_name in contact_lower:
            return True

        # Tier 2: punctuation-stripped containment
        if contact_clean and (contact_clean in inbox_clean or inbox_clean in contact_clean):
            return True

        # Tier 3: abbreviated last name (e.g. "Aravind S.")
        tokens_clean = contact_clean.split()
        first_token = tokens_clean[0] if tokens_clean else ""
        tokens_orig = contact_lower.split()
        last_token = tokens_orig[-1] if tokens_orig else ""
        is_abbreviated = len(_strip_punct(last_token)) <= 1 or last_token.endswith(".")
        if len(first_token) >= 4 and is_abbreviated and first_token in inbox_clean:
            return True

    return False


# ── Pitch / chat helpers ───────────────────────────────────────────────────────

def _create_inbound_first_pitch(
    contact: LinkedinSearchContact | LinkedinContact,
    contact_type: str,
    profile_id: int,
    db: Session,
) -> SalesPitch:
    """
    Create a stub SalesPitch with delivery_status='inbound_first'.
    No pitch body is generated — this record exists purely to anchor
    ClientChatMessage rows and to be picked up by the follow-up checker.
    """
    kwargs: dict = {
        "pitch_channel": "linkedin",
        "delivery_status": "inbound_first",
        "delivered_at": datetime.now(timezone.utc),
        "pitch_body": "",
        "pitch_subject": "",
    }
    if contact_type == "LinkedinSearchContact":
        kwargs["linkedin_search_contact_id"] = contact.id
    else:
        kwargs["linkedin_contact_id"] = contact.id

    pitch = SalesPitch(**kwargs)
    db.add(pitch)
    db.commit()
    db.refresh(pitch)
    logger.info(
        f"[INBOUND FIRST] 📝 Created stub SalesPitch id={pitch.id} "
        f"(delivery_status='inbound_first') for {contact.name!r}"
    )
    return pitch


def _persist_inbound_messages(
    pitch: SalesPitch,
    contact: LinkedinSearchContact | LinkedinContact,
    contact_type: str,
    profile_id: int,
    messages: list[dict],
    suggested_reply: str | None,
    telegram_chat_id: str | None,
    db: Session,
) -> None:
    """
    Persist the scraped thread messages into client_chat_messages.
    De-duplicates by (sales_pitch_id, sender, message_body).
    """
    if not messages:
        return

    contact_id = contact.id if contact_type == "LinkedinContact" else None
    search_contact_id = contact.id if contact_type == "LinkedinSearchContact" else None

    for idx, msg in enumerate(messages):
        body = (msg.get("body") or "").strip()
        if not body:
            continue

        is_self = bool(msg.get("is_self"))
        sender = "You" if is_self else (msg.get("sender") or "Them")

        existing = (
            db.query(ClientChatMessage)
            .filter(
                ClientChatMessage.sales_pitch_id == pitch.id,
                ClientChatMessage.sender == sender,
                ClientChatMessage.message_body == body,
            )
            .first()
        )
        if existing:
            continue

        is_last = idx == len(messages) - 1
        record = ClientChatMessage(
            profile_id=profile_id,
            sales_pitch_id=pitch.id,
            linkedin_contact_id=contact_id,
            linkedin_search_contact_id=search_contact_id,
            sender=sender,
            message_body=body,
            is_self=is_self,
            suggested_reply=suggested_reply if (is_last and not is_self) else None,
            telegram_chat_id=telegram_chat_id if (is_last and not is_self) else None,
            status="received" if not is_self else "sent",
            conversation_active=True,
        )
        db.add(record)

    db.commit()
    logger.info(
        f"[INBOUND FIRST] 💾 Persisted {len(messages)} message(s) for pitch {pitch.id}"
    )


# ── Main entry point ───────────────────────────────────────────────────────────

def handle_inbound_first_contacts(
    newly_accepted_contacts: list[tuple],   # list of (contact_obj, contact_type_str)
    inbox_scan_result: dict,
    profile_id: int,
    db: Session,
) -> int:
    """
    Cross-reference newly-accepted contacts against the inbox scan result.
    For any contact whose name/slug appears in the unread conversation list,
    run the full inbound-first pipeline.

    Parameters
    ----------
    newly_accepted_contacts : list of (contact_obj, contact_type_str)
        Each element is a 2-tuple: (LinkedinSearchContact | LinkedinContact, type_label).
    inbox_scan_result : dict
        The result dict returned by scan_inbox() — may contain all conversations
        or just unread ones.
    profile_id : int
        The LinkedIn account profile running the check.
    db : Session
        Active SQLAlchemy session.

    Returns
    -------
    int  — number of contacts handled as inbound-first.
    """
    if not newly_accepted_contacts:
        return 0

    # Use ALL conversations (not just unread) when looking for inbound-first messages.
    # The contact may have sent a message that LinkedIn didn't mark as unread yet,
    # or we may have already seen it.  We filter below by preview content.
    all_convs = inbox_scan_result.get("conversations") or []

    if not all_convs:
        logger.info(
            "[INBOUND FIRST] ℹ️  No inbox conversations available — "
            "skipping inbound-first check."
        )
        return 0

    # Build lookup sets
    inbox_slugs: dict[str, dict] = {}   # slug → conversation dict
    inbox_names: dict[str, dict] = {}   # lower_name → conversation dict
    for conv in all_convs:
        slug = conv.get("profile_slug")
        if slug:
            inbox_slugs[slug] = conv
        name = (conv.get("name") or "").strip().lower()
        if name:
            inbox_names[name] = conv

    handled = 0

    for contact, contact_type in newly_accepted_contacts:
        if not contact:
            continue

        contact_name = getattr(contact, "name", None) or ""
        contact_slug = _extract_slug(getattr(contact, "profile_url", None))

        # ── Is this contact already in the inbox? ─────────────────────────────
        matched_conv: dict | None = None

        if contact_slug and contact_slug in inbox_slugs:
            matched_conv = inbox_slugs[contact_slug]
        elif contact_name:
            all_inbox_names = set(inbox_names.keys())
            if _name_matches(contact_name, all_inbox_names):
                # Find the matching conv dict
                contact_lower = contact_name.strip().lower()
                for iname, conv in inbox_names.items():
                    if _name_matches(contact_lower, {iname}):
                        matched_conv = conv
                        break

        if not matched_conv:
            logger.debug(
                f"[INBOUND FIRST] ➡️  {contact_name!r} not found in inbox — "
                "will receive normal pitch delivery."
            )
            continue

        # ── Contact IS in inbox: check if they messaged us ───────────────────
        # The preview text from the inbox card already tells us the last message.
        # If preview does NOT start with "You:", they sent the last message.
        preview: str = matched_conv.get("preview") or ""
        if preview.lower().startswith("you:"):
            logger.info(
                f"[INBOUND FIRST] ℹ️  {contact_name!r} — last inbox message is ours "
                "(preview starts 'You:'). Not inbound-first."
            )
            continue

        logger.info(
            f"[INBOUND FIRST] 🚨 {contact_name!r} appears to have messaged us first! "
            f"Preview: {preview[:80]!r} — opening thread to capture full history."
        )

        # ── Skip if we already have an inbound_first pitch for this contact ──
        existing_pitch_filter = (
            SalesPitch.delivery_status == "inbound_first",
            SalesPitch.pitch_channel == "linkedin",
        )
        if contact_type == "LinkedinSearchContact":
            existing_pitch_filter += (
                SalesPitch.linkedin_search_contact_id == contact.id,
            )
        else:
            existing_pitch_filter += (
                SalesPitch.linkedin_contact_id == contact.id,
            )

        existing_pitch = db.query(SalesPitch).filter(*existing_pitch_filter).first()
        if existing_pitch:
            logger.info(
                f"[INBOUND FIRST] ⏭️  {contact_name!r} already has an inbound_first "
                f"pitch (id={existing_pitch.id}) — skipping."
            )
            continue

        # ── Open the thread and capture the full chat history ─────────────────
        try:
            result = check_reply_for_contact(
                profile_url=getattr(contact, "profile_url", None) or "",
                contact_name=contact_name,
                db=db,
                delivered_at=None,   # no delivery yet
                profile_id=profile_id,
            )
        except LinkedInSessionExpiredError:
            logger.error(
                "[INBOUND FIRST] ⛔ LinkedIn session expired while opening "
                f"thread for {contact_name!r} — stopping inbound-first check."
            )
            raise

        except Exception as exc:
            logger.error(
                f"[INBOUND FIRST] ❌ Error opening thread for {contact_name!r}: {exc}"
            )
            continue

        messages: list[dict] = result.get("chat_history") or []

        # Final guard: confirm last message is truly from them
        if not messages:
            logger.info(
                f"[INBOUND FIRST] ℹ️  Thread for {contact_name!r} returned no messages."
            )
            continue

        last_msg = messages[-1]
        if last_msg.get("is_self"):
            logger.info(
                f"[INBOUND FIRST] ℹ️  Last message in thread for {contact_name!r} "
                "is ours — not inbound-first."
            )
            continue

        # ── Run the full pipeline ─────────────────────────────────────────────

        # 1. Create stub pitch
        pitch = _create_inbound_first_pitch(contact, contact_type, profile_id, db)

        # 2. Generate LLM suggested reply
        try:
            from app.services.pitch_reply_service import generate_suggested_reply
            suggested_reply = generate_suggested_reply(
                contact_name=contact_name,
                company_name="",
                pitch_context="",   # no pitch yet
                messages=messages,
            )
        except Exception as exc:
            logger.warning(
                f"[INBOUND FIRST] ⚠️  LLM suggestion failed for {contact_name!r}: {exc}"
            )
            suggested_reply = None

        # 3. Send Telegram notification
        try:
            telegram_chat_id = notify_reply_received(
                contact_name=contact_name,
                company_name="",
                reply_snippet=last_msg["body"],
                profile_id=profile_id,
                suggested_reply=suggested_reply,
                sales_pitch_id=pitch.id,
            )
        except Exception as exc:
            logger.warning(
                f"[INBOUND FIRST] ⚠️  Telegram notification failed for {contact_name!r}: {exc}"
            )
            telegram_chat_id = None

        # 4. Persist all messages
        _persist_inbound_messages(
            pitch=pitch,
            contact=contact,
            contact_type=contact_type,
            profile_id=profile_id,
            messages=messages,
            suggested_reply=suggested_reply,
            telegram_chat_id=telegram_chat_id,
            db=db,
        )

        # 5. Mark contact so pitch delivery skips them
        if contact_type == "LinkedinSearchContact":
            contact.contact_messaged_first = True
            db.commit()
            logger.info(
                f"[INBOUND FIRST] 🏷️  Flagged {contact_name!r} as contact_messaged_first=True"
            )

        handled += 1
        logger.info(
            f"[INBOUND FIRST] ✅ Inbound-first pipeline complete for {contact_name!r} "
            f"(pitch id={pitch.id}, messages={len(messages)})"
        )

    if handled:
        logger.info(
            f"[INBOUND FIRST] 🎉 Handled {handled} inbound-first contact(s) this run."
        )
    else:
        logger.info(
            "[INBOUND FIRST] ℹ️  No inbound-first contacts detected this run."
        )

    return handled
