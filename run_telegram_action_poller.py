from __future__ import annotations

import os
import sys
import time
import logging
from datetime import datetime, timezone

# ── Make project imports work when running from root ────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy.orm import Session

from app.config.database import SessionLocal
from app.models.telegram_pending_action import TelegramPendingAction
from app.models.client_chat_message import ClientChatMessage
from app.models.sales_pitch import SalesPitch
from app.services.pitch_reply_service import _resolve_contact
from app.scraper.linkedin_dm_sender import send_linkedin_dm
from app.utils.logger import logger

# ── Config ────────────────────────────────────────────────────────────────────

POLL_INTERVAL = int(os.getenv("TELEGRAM_POLLER_INTERVAL_SEC", "60"))

# ── Core processing logic ─────────────────────────────────────────────────────

def _process_action(row: TelegramPendingAction, db: Session) -> None:
    """
    Execute the reply-callback logic for a single pending action row.
    Mirrors the logic in handle_webhook_reply_callback() in lead_score.py.

    Raises on failure — caller marks the row as failed.
    """
    chat_id    = row.chat_id
    pitch_id   = row.pitch_id
    profile_id = row.profile_id or 1
    action_str = (row.action or "").strip()
    action_lower = action_str.lower()

    logger.info(
        f"[POLLER] Processing action id={row.id} | chat_id={chat_id} "
        f"| pitch_id={pitch_id} | action={action_str!r}"
    )

    # ── Resolve the ClientChatMessage ────────────────────────────────────────
    query = db.query(ClientChatMessage)
    if chat_id:
        query = query.filter(ClientChatMessage.telegram_chat_id == chat_id)
    elif pitch_id:
        query = query.filter(ClientChatMessage.sales_pitch_id == pitch_id)
    else:
        raise ValueError("Action row has neither chat_id nor pitch_id — cannot resolve message")

    chat_msg = query.order_by(ClientChatMessage.id.desc()).first()
    if not chat_msg:
        raise ValueError(f"ClientChatMessage not found for chat_id={chat_id} / pitch_id={pitch_id}")

    # ── Resolve the SalesPitch ───────────────────────────────────────────────
    resolved_pitch_id = pitch_id or chat_msg.sales_pitch_id
    pitch = db.query(SalesPitch).filter(SalesPitch.id == resolved_pitch_id).first()
    if not pitch:
        raise ValueError(f"SalesPitch id={resolved_pitch_id} not found")

    # ── Resolve the LinkedIn contact ─────────────────────────────────────────
    contact, contact_type = _resolve_contact(pitch, db)
    if not contact:
        raise ValueError(f"No linked contact found for pitch id={resolved_pitch_id}")

    # ── Handle reject actions ────────────────────────────────────────────────
    if action_lower in ("no", "reject", "cancel"):
        chat_msg.status = "rejected"
        db.commit()
        logger.info(f"[POLLER] ✅ Action id={row.id} → rejected, no DM sent.")
        return

    # ── Determine text to send ───────────────────────────────────────────────
    if action_lower == "yes":
        text_to_send = (
            chat_msg.suggested_reply
            or "Thank you for getting in touch! We'd be happy to discuss further."
        )
    else:
        text_to_send = action_str

    # ── Send the LinkedIn DM via Playwright ──────────────────────────────────
    logger.info(f"[POLLER] 📨 Sending LinkedIn DM to {getattr(contact, 'name', 'contact')!r}…")
    sent_ok = send_linkedin_dm(
        contact    = contact,
        message    = text_to_send,
        db         = db,
        profile_id = profile_id,
    )

    if sent_ok:
        chat_msg.status         = "approved" if action_lower == "yes" else "custom_sent"
        chat_msg.sent_reply_text = text_to_send

        # Record the sent message in chat history
        new_msg = ClientChatMessage(
            profile_id                 = profile_id,
            sales_pitch_id             = pitch.id,
            linkedin_contact_id        = pitch.linkedin_contact_id,
            linkedin_search_contact_id = pitch.linkedin_search_contact_id,
            sender                     = "You",
            message_body               = text_to_send,
            is_self                    = True,
            status                     = "sent",
        )
        db.add(new_msg)
        db.commit()
        logger.info(
            f"[POLLER] ✅ DM sent to {getattr(contact, 'name', 'contact')!r} "
            f"(action id={row.id})"
        )
    else:
        chat_msg.status = "delivery_failed"
        db.commit()
        raise RuntimeError(
            f"send_linkedin_dm returned False for contact {getattr(contact, 'name', '?')!r}"
        )


# ── Poll loop ─────────────────────────────────────────────────────────────────

def poll_once(db: Session) -> int:
    """
    Process all pending rows in one poll cycle.
    Returns the number of rows processed.
    """
    rows = (
        db.query(TelegramPendingAction)
        .filter(TelegramPendingAction.status == "pending")
        .order_by(TelegramPendingAction.created_at.asc())
        .all()
    )

    if not rows:
        return 0

    logger.info(f"[POLLER] 🔍 Found {len(rows)} pending action(s) to process.")

    processed = 0
    for row in rows:
        # ── Mark as processing to prevent double-processing ────────────────
        row.status = "processing"
        try:
            db.commit()
        except Exception:
            db.rollback()
            continue   # another poller instance got there first

        # ── Process ────────────────────────────────────────────────────────
        try:
            _process_action(row, db)
            row.status       = "done"
            row.processed_at = datetime.now(timezone.utc)
            db.commit()
            processed += 1
        except Exception as exc:
            db.rollback()
            logger.error(f"[POLLER] ❌ Action id={row.id} failed: {exc}")
            try:
                row.status        = "failed"
                row.error_message = str(exc)[:2000]
                row.processed_at  = datetime.now(timezone.utc)
                db.commit()
            except Exception:
                db.rollback()

    return processed


def main() -> None:
    logger.info(
        f"[POLLER] 🚀 Telegram action poller started — "
        f"polling every {POLL_INTERVAL}s"
    )
    logger.info("[POLLER] Press Ctrl+C to stop.")

    while True:
        db = SessionLocal()
        try:
            count = poll_once(db)
            if count:
                logger.info(f"[POLLER] ✅ Processed {count} action(s) this cycle.")
            else:
                logger.debug("[POLLER] No pending actions.")
        except Exception as exc:
            logger.error(f"[POLLER] ❌ Unexpected poll error: {exc}")
        finally:
            db.close()

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
