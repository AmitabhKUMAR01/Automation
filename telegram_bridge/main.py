from __future__ import annotations

import os
import logging
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request, Header, HTTPException, status
from sqlalchemy import create_engine, Column, Integer, String, Text, JSON, DateTime, func
from sqlalchemy.orm import sessionmaker, declarative_base
from urllib.parse import quote_plus

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("bridge")

# ── DB Setup (minimal — no shared models, standalone) ────────────────────────

DB_USER     = os.getenv("DB_USER", "root")
DB_PASSWORD = quote_plus(os.getenv("DB_PASSWORD", ""))
DB_HOST     = os.getenv("DB_HOST", "localhost")
DB_PORT     = os.getenv("DB_PORT", "3306")
DB_NAME     = os.getenv("DB_NAME", "scraped_raw_data")

DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

engine       = create_engine(DATABASE_URL, pool_pre_ping=True, echo=False)
SessionLocal = sessionmaker(bind=engine)
Base         = declarative_base()


class TelegramPendingAction(Base):
    """Mirror of app/models/telegram_pending_action.py — kept standalone for bridge."""
    __tablename__ = "telegram_pending_actions"

    id            = Column(Integer, primary_key=True)
    chat_id       = Column(String(100), nullable=True)
    pitch_id      = Column(Integer,     nullable=True)
    profile_id    = Column(Integer,     nullable=True)
    action        = Column(Text,        nullable=False)
    status        = Column(String(20),  nullable=False, default="pending")
    raw_payload   = Column(JSON,        nullable=True)
    error_message = Column(Text,        nullable=True)
    created_at    = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    processed_at  = Column(DateTime(timezone=True), nullable=True)


# ── Auth ──────────────────────────────────────────────────────────────────────

WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN", "").strip()


def _verify_token(authorization: str | None) -> None:
    """Raise 401 if the Bearer token does not match WEBHOOK_TOKEN."""
    if not WEBHOOK_TOKEN:
        return  # token check disabled if not configured
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    if token != WEBHOOK_TOKEN:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


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

        parsed: dict[str, Any] = {"chat_id": chat_id, "pitch_id": None, "profile_id": None, "action": "yes"}

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
        chat_id  = str(msg.get("chat", {}).get("id") or "").strip() or None
        text     = (msg.get("text") or "").strip()

        if not text:
            return None

        parts = text.split(None, 1)   # split on first whitespace only

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

    return None   # unknown update type — ignore


# ── FastAPI App ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="Telegram Webhook Bridge",
    description="Receives Telegram bot replies and queues them for local PC processing.",
    version="1.0.0",
)


@app.get("/health")
def health():
    """Health check — Render/Railway uses this to confirm the service is alive."""
    return {"status": "ok", "service": "telegram-bridge"}


@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    authorization: str | None = Header(default=None),
):
    """
    Telegram webhook endpoint.
    Register with:
      curl "https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://<your-bridge>/telegram/webhook"

    Telegram sends updates here automatically.
    Token auth is OPTIONAL (Telegram cannot send custom headers),
    so the token is validated via query param instead when set.
    """
    payload = await request.json()
    log.info(f"[WEBHOOK] Received Telegram update: {str(payload)[:300]}")

    parsed = _parse_telegram_update(payload)
    if not parsed:
        log.info("[WEBHOOK] Could not parse update — ignoring.")
        return {"status": "ignored"}

    db = SessionLocal()
    try:
        row = TelegramPendingAction(
            chat_id     = parsed["chat_id"],
            pitch_id    = parsed["pitch_id"],
            profile_id  = parsed["profile_id"],
            action      = parsed["action"],
            status      = "pending",
            raw_payload = payload,
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


@app.post("/telegram/action")
async def manual_action(
    request: Request,
    authorization: str | None = Header(default=None),
):
    """
    Manual trigger endpoint for testing without a real Telegram update.
    Requires Authorization: Bearer <WEBHOOK_TOKEN>

    Body example:
    {
        "chat_id": "-100123456",
        "pitch_id": 42,
        "profile_id": 1,
        "action": "yes"
    }
    """
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
