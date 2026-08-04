from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Request, Header, HTTPException, Query, status
from pydantic import BaseModel

from .db import SessionLocal, TelegramPendingAction

log = logging.getLogger("bridge.router")

router = APIRouter()


# ── Auth helper ───────────────────────────────────────────────────────────────

import os

WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN", "").strip()


def _verify_token(authorization: str | None) -> None:
    """Raise 401 if the Bearer token does not match WEBHOOK_TOKEN."""
    if not WEBHOOK_TOKEN:
        return  # token check disabled if not configured
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Bearer token",
        )
    token = authorization.removeprefix("Bearer ").strip()
    if token != WEBHOOK_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )


# ── Telegram payload parser ───────────────────────────────────────────────────

def _parse_telegram_update(payload: dict) -> dict | None:
    """
    Extract (chat_id, pitch_id, profile_id, action) from a Telegram update.

    Supports two incoming formats:

    1. Inline keyboard callback_query (preferred):
       callback_data format: "pitch_id=42|profile_id=1|action=yes"

    2. Plain text message (fallback):
       Text format: "<pitch_id> <action>"
       Example: "42 yes"   or   "42 Thanks, let's schedule a call!"
    """
    # ── Format 1: inline keyboard callback ───────────────────────────────────
    cq = payload.get("callback_query")
    if cq:
        chat_id  = str(cq.get("message", {}).get("chat", {}).get("id") or "").strip() or None
        data_str = (cq.get("data") or "").strip()

        parsed: dict[str, Any] = {
            "chat_id": chat_id,
            "pitch_id": None,
            "profile_id": None,
            "action": "yes",
        }

        for part in data_str.split("|"):
            if "=" in part:
                k, _, v = part.partition("=")
                k = k.strip()
                v = v.strip()
                if k == "pitch_id":
                    try:
                        parsed["pitch_id"] = int(v)
                    except ValueError:
                        pass
                elif k == "profile_id":
                    try:
                        parsed["profile_id"] = int(v)
                    except ValueError:
                        pass
                elif k == "action":
                    parsed["action"] = v

        if parsed["action"]:
            return parsed

    # ── Format 2: plain text message ─────────────────────────────────────────
    msg = payload.get("message")
    if msg:
        chat_id = str(msg.get("chat", {}).get("id") or "").strip() or None
        text    = (msg.get("text") or "").strip()

        if not text:
            return None

        parts = text.split(None, 1)  # split on first whitespace only

        pitch_id = None
        if parts and parts[0].isdigit():
            pitch_id = int(parts[0])
            action   = parts[1].strip() if len(parts) > 1 else "yes"
        else:
            # No leading pitch_id — treat whole text as action (uses chat_id lookup)
            action = text

        return {
            "chat_id"   : chat_id,
            "pitch_id"  : pitch_id,
            "profile_id": None,
            "action"    : action,
        }

    return None  # unknown update type — ignore


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class StatusUpdateRequest(BaseModel):
    """Request body for PATCH /pending-actions/{id}/status."""
    status: str
    error_message: Optional[str] = None


class PendingActionOut(BaseModel):
    """Serialized response for a single TelegramPendingAction row."""
    id           : int
    chat_id      : Optional[str]
    pitch_id     : Optional[int]
    profile_id   : Optional[int]
    action       : str
    status       : str
    raw_payload  : Optional[Any]
    error_message: Optional[str]
    created_at   : datetime
    processed_at : Optional[datetime]

    model_config = {"from_attributes": True}


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/health")
def health():
    """Health check — Render/Railway uses this to confirm the service is alive."""
    return {"status": "ok", "service": "telegram-bridge"}


@router.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    authorization: str | None = Header(default=None),
):

    payload = await request.json()
    log.info(f"[WEBHOOK] Received Telegram update: {str(payload)[:300]}")

    parsed = _parse_telegram_update(payload)
    if not parsed:
        log.info("[WEBHOOK] Could not parse update — ignoring.")
        return {"status": "ignored"}

    db = SessionLocal()
    try:
        row = TelegramPendingAction(
            chat_id    = parsed["chat_id"],
            pitch_id   = parsed["pitch_id"],
            profile_id = parsed["profile_id"],
            action     = parsed["action"],
            status     = "pending",
            raw_payload= payload,
        )
        db.add(row)
        db.commit()
        log.info(
            f"[WEBHOOK] Queued action id={row.id} | chat_id={parsed['chat_id']} "
            f"| pitch_id={parsed['pitch_id']} | action={parsed['action']!r}"
        )
    except Exception as exc:
        db.rollback()
        log.error(f"[WEBHOOK] DB write failed: {exc}")
        # Still return 200 — Telegram retries on non-200, causing duplicates
        return {"status": "error", "detail": str(exc)}
    finally:
        db.close()

    return {"status": "queued"}


@router.post("/telegram/action")
async def manual_action(
    request: Request,
    authorization: str | None = Header(default=None),
):
    _verify_token(authorization)
    body = await request.json()

    action = (body.get("action") or "").strip()
    if not action:
        raise HTTPException(status_code=400, detail="'action' field is required")

    db = SessionLocal()
    try:
        row = TelegramPendingAction(
            chat_id    = str(body.get("chat_id") or "").strip() or None,
            pitch_id   = body.get("pitch_id"),
            profile_id = body.get("profile_id"),
            action     = action,
            status     = "pending",
            raw_payload= body,
        )
        db.add(row)
        db.commit()
        return {"status": "queued", "id": row.id}
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        db.close()


# ── Fetch / inspect endpoints ─────────────────────────────────────────────────

VALID_STATUSES = {"pending", "processing", "done", "failed"}


@router.get("/pending-actions", response_model=dict)
def list_pending_actions(
    authorization: str | None = Header(default=None),
    page    : int           = Query(1, ge=1, description="Page number (1-indexed)"),
    per_page: int           = Query(20, ge=1, le=100, description="Items per page"),
    status  : Optional[str] = Query(
        "pending",
        description="Filter by status: pending | processing | done | failed | all",
    ),
):
    _verify_token(authorization)

    db = SessionLocal()
    try:
        q = db.query(TelegramPendingAction)

        if status and status.lower() != "all":
            q = q.filter(TelegramPendingAction.status == status.lower())

        q = q.order_by(TelegramPendingAction.created_at.desc())

        total = q.count()
        rows  = q.offset((page - 1) * per_page).limit(per_page).all()

        items = [PendingActionOut.model_validate(r).model_dump(mode="json") for r in rows]

        return {
            "status": "ok",
            "data": {
                "items": items,
                "pagination": {
                    "total"       : total,
                    "per_page"    : per_page,
                    "current_page": page,
                    "last_page"   : max(1, (total + per_page - 1) // per_page),
                },
                "filter": {"status": status or "all"},
            },
        }
    except Exception as exc:
        log.error(f"[API] list_pending_actions failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        db.close()


@router.get("/pending-actions/{action_id}", response_model=dict)
def get_pending_action(
    action_id    : int,
    authorization: str | None = Header(default=None),
):
    _verify_token(authorization)

    db = SessionLocal()
    try:
        row = db.query(TelegramPendingAction).filter(
            TelegramPendingAction.id == action_id
        ).first()

        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"Pending action id={action_id} not found",
            )

        return {
            "status": "ok",
            "data"  : PendingActionOut.model_validate(row).model_dump(mode="json"),
        }
    except HTTPException:
        raise
    except Exception as exc:
        log.error(f"[API] get_pending_action({action_id}) failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        db.close()


@router.patch("/pending-actions/{action_id}/status", response_model=dict)
def update_pending_action_status(
    action_id    : int,
    body         : StatusUpdateRequest,
    authorization: str | None = Header(default=None),
):
    _verify_token(authorization)

    new_status = body.status.lower().strip()
    if new_status not in VALID_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status '{new_status}'. Must be one of: {', '.join(sorted(VALID_STATUSES))}",
        )

    db = SessionLocal()
    try:
        row = db.query(TelegramPendingAction).filter(
            TelegramPendingAction.id == action_id
        ).first()

        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"Pending action id={action_id} not found",
            )

        row.status = new_status

        if body.error_message is not None:
            row.error_message = body.error_message

        # Auto-stamp processed_at when transitioning to a terminal state
        if new_status in ("done", "failed") and row.processed_at is None:
            row.processed_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(row)

        log.info(
            f"[API] Action id={action_id} status updated to '{new_status}'"
            + (f" | error={body.error_message!r}" if body.error_message else "")
        )

        return {
            "status": "ok",
            "data"  : PendingActionOut.model_validate(row).model_dump(mode="json"),
        }
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        log.error(f"[API] update_pending_action_status({action_id}) failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        db.close()
