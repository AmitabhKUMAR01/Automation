from __future__ import annotations

import os
import sys
import time
import logging
from datetime import datetime, timezone
from typing import Any

import requests

# ── Make project imports work when running from root ────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy.orm import Session

from app.config.database import SessionLocal
from app.models.client_chat_message import ClientChatMessage
from app.models.sales_pitch import SalesPitch
from app.services.pitch_reply_service import _resolve_contact
from app.scraper.linkedin_dm_sender import send_linkedin_dm
from app.utils.logger import logger

# ── Config ────────────────────────────────────────────────────────────────────

POLL_INTERVAL    = int(os.getenv("TELEGRAM_POLLER_INTERVAL_SEC", "60"))
BRIDGE_API_URL   = os.getenv("BRIDGE_API_URL", "https://bridge.pandanext.in")
BRIDGE_API_TOKEN = os.getenv("BRIDGE_API_TOKEN", "iqh-3MuMOfsedcLu4VnGeUdZs8Sajkx1CbdNlYcgN60")


# ── Bridge API helpers ────────────────────────────────────────────────────────

def _fetch_pending_actions(page: int = 1, per_page: int = 50) -> list[dict]:
    """
    Fetch pending actions from the bridge webhook API.
    Returns a flat list of action dicts from all pages.
    """
    url     = f"{BRIDGE_API_URL}/pending-actions"
    headers = {
        "accept":        "application/json",
        "Authorization": f"Bearer {BRIDGE_API_TOKEN}",
    }
    all_items: list[dict] = []
    current_page = page

    while True:
        params = {"page": current_page, "per_page": per_page, "status": "pending"}
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error(f"[POLLER] ❌ Failed to fetch pending actions (page={current_page}): {exc}")
            break

        body = resp.json()
        items = body.get("data", {}).get("items", [])
        all_items.extend(items)

        pagination = body.get("data", {}).get("pagination", {})
        if current_page >= pagination.get("last_page", 1):
            break
        current_page += 1

    return all_items


def _mark_action_status(
    action_id: int,
    status: str,
    error_message: str | None = None,
) -> None:
    """
    PATCH the bridge API to update an action's status (e.g. 'done' or 'failed').
    """
    url     = f"{BRIDGE_API_URL}/pending-actions/{action_id}/status"
    headers = {
        "accept":        "application/json",
        "Content-Type":  "application/json",
        "Authorization": f"Bearer {BRIDGE_API_TOKEN}",
    }
    payload: dict[str, Any] = {
        "status":        status,
        "error_message": (error_message or "")[:2000],
    }

    try:
        resp = requests.patch(url, headers=headers, json=payload, timeout=15)
        resp.raise_for_status()
        logger.debug(f"[POLLER] Bridge action id={action_id} marked as '{status}'.")
    except requests.RequestException as exc:
        logger.warning(
            f"[POLLER] ⚠️  Could not update bridge action id={action_id} status to '{status}': {exc}"
        )


# ── Core processing logic ─────────────────────────────────────────────────────

def _process_action(action: dict, db: Session) -> None:
    """
    Execute the reply-callback logic for a single pending action dict
    (as returned by the bridge webhook API).

    Raises on failure — caller marks the action as failed.
    """
    action_id    = action["id"]
    # chat_id / reply_chat_id come from the webhook payload
    chat_id      = action.get("reply_chat_id") or action.get("chat_id")
    action_str   = (action.get("action") or "").strip()
    action_lower = action_str.lower()

    logger.info(
        f"[POLLER] Processing action id={action_id} | chat_id={chat_id} "
        f"| action={action_str!r}"
    )

    # ── Resolve the ClientChatMessage via telegram_chat_id ───────────────────
    if not chat_id:
        raise ValueError(f"Action id={action_id} has no usable chat_id / reply_chat_id")

    chat_msg = (
        db.query(ClientChatMessage)
        .filter(ClientChatMessage.telegram_chat_id == str(chat_id))
        .order_by(ClientChatMessage.id.desc())
        .first()
    )
    if not chat_msg:
        raise ValueError(
            f"ClientChatMessage not found for telegram_chat_id={chat_id!r}"
        )

    # ── Resolve profile_id dynamically ───────────────────────────────────────
    raw_payload = action.get("raw_payload") or {}
    if isinstance(raw_payload, str):
        import json
        try:
            raw_payload = json.loads(raw_payload)
        except Exception:
            raw_payload = {}
            
    profile_id = (
        action.get("profile_id")
        or raw_payload.get("profile_id")
        or chat_msg.profile_id
        or 1
    )

    # ── Resolve the SalesPitch ───────────────────────────────────────────────
    pitch = db.query(SalesPitch).filter(SalesPitch.id == chat_msg.sales_pitch_id).first()
    if not pitch:
        raise ValueError(f"SalesPitch id={chat_msg.sales_pitch_id} not found")

    # ── Resolve the LinkedIn contact ─────────────────────────────────────────
    contact, contact_type = _resolve_contact(pitch, db)
    if not contact:
        raise ValueError(f"No linked contact found for pitch id={pitch.id}")

    # ── Handle reject actions ────────────────────────────────────────────────
    if action_lower in ("n", "no", "reject", "cancel"):
        chat_msg.status = "rejected"
        db.commit()
        logger.info(f"[POLLER] ✅ Action id={action_id} → rejected, no DM sent.")
        return

    # ── Determine text to send ───────────────────────────────────────────────
    if action_lower in ("yes", "y"):
        text_to_send = (
            chat_msg.suggested_reply
            or "Thank you for getting in touch! We'd be happy to discuss further."
        )
    elif action_str.startswith("/"):
        text_to_send = action_str[1:]
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
        chat_msg.status          = "approved" if action_lower in ("yes", "y") else "custom_sent"
        chat_msg.sent_reply_text = text_to_send

        new_msg = ClientChatMessage(
            profile_id                 = profile_id,
            sales_pitch_id             = pitch.id,
            linkedin_contact_id        = pitch.linkedin_contact_id,
            linkedin_search_contact_id = pitch.linkedin_search_contact_id,
            sender                     = "You",
            message_body               = text_to_send,
            is_self                    = True,
            status                     = "sent",
            conversation_active        = True,   # ← enables follow-up checking for this thread
        )
        db.add(new_msg)

        pitch.reply_checked_at = datetime.now(timezone.utc)

        db.commit()
        logger.info(
            f"[POLLER] ✅ DM sent to {getattr(contact, 'name', 'contact')!r} "
            f"(action id={action_id})"
        )
    else:
        chat_msg.status = "delivery_failed"
        db.commit()
        raise RuntimeError(
            f"send_linkedin_dm returned False for contact {getattr(contact, 'name', '?')!r}"
        )


# ── Poll loop ─────────────────────────────────────────────────────────────────

def poll_once(db: Session) -> int:
    actions = _fetch_pending_actions()

    if not actions:
        return 0

    logger.info(f"[POLLER] 🔍 Found {len(actions)} pending action(s) to process.")

    processed = 0
    for action in actions:
        action_id = action["id"]

        # Optimistically mark as processing on the bridge so other poller
        # instances (if any) skip this action.
        _mark_action_status(action_id, "processing")

        # ── Process ────────────────────────────────────────────────────────
        try:
            _process_action(action, db)
            _mark_action_status(action_id, "done")
            processed += 1
        except Exception as exc:
            db.rollback()
            logger.error(f"[POLLER] ❌ Action id={action_id} failed: {exc}")
            _mark_action_status(action_id, "failed", error_message=str(exc))

    return processed

def _run_once() -> None:
    """Single poll cycle — used when invoked from cron."""
    db = SessionLocal()
    try:
        count = poll_once(db)
        if count:
            logger.info(f"[POLLER] ✅ Processed {count} action(s).")
        else:
            logger.info("[POLLER] No pending actions.")
    except Exception as exc:
        logger.error(f"[POLLER] ❌ Unexpected poll error: {exc}")
    finally:
        db.close()


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Telegram action poller")
    parser.add_argument(
        "--once", "-1",
        action="store_true",
        help="Run a single poll cycle and exit (use this from cron).",
    )
    args = parser.parse_args()

    if args.once:
        logger.info("[POLLER] 🕐 Running single poll cycle (cron mode)…")
        _run_once()
        return

    # ── Daemon mode (default) ─────────────────────────────────────────────────
    logger.info(
        f"[POLLER] 🚀 Telegram action poller started — "
        f"polling every {POLL_INTERVAL}s"
    )
    logger.info("[POLLER] Press Ctrl+C to stop.")

    while True:
        _run_once()
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
