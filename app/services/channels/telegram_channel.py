from __future__ import annotations
import os
from datetime import datetime, timezone, timedelta
try:
    from zoneinfo import ZoneInfo
    _IST = ZoneInfo("Asia/Kolkata")
except ImportError:
    _IST = timezone(timedelta(hours=5, minutes=30))   # fallback: UTC+5:30

import requests
from app.utils.logger import logger
from .base import NotificationChannel


# ── Action ID labels (human-readable name per event type) ─────────────────────
_ACTION_ID_NAMES: dict[str, str] = {
    "job_failure"        : "Scheduler Job Failure",
    "connection_accepted": "LinkedIn Connection Accepted",
    "reply_received"     : "LinkedIn Reply Received",
}


def _build_base(event: str) -> dict:
    """Return common fields added to every payload."""
    return {
        "client_name"   : "HPLinkdinBot",
        "timezone"      : "Asia/Kolkata",
        "event_time"    : datetime.now(_IST).strftime("%Y-%m-%d %H:%M:%S"),
        "action_id_name": _ACTION_ID_NAMES.get(event, event),
    }


def _post(payload: dict) -> bool:
    """POST a JSON payload to the webhook URL with Bearer token auth."""
    webhook_url = os.getenv("WEBHOOK_URL", "").strip()
    webhook_token = os.getenv("WEBHOOK_TOKEN", "").strip()

    if not webhook_url:
        logger.warning("[NOTIFY:TELEGRAM] WEBHOOK_URL not set — notification skipped.")
        return True  # not a failure, just unconfigured

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if webhook_token:
        headers["Authorization"] = f"Bearer {webhook_token}"

    try:
        response = requests.post(webhook_url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        logger.info("[NOTIFY:TELEGRAM] ✅ Webhook alert sent.")
        return True

    except requests.exceptions.Timeout:
        logger.warning("[NOTIFY:TELEGRAM] ❌ Webhook request timed out.")
    except requests.exceptions.ConnectionError:
        logger.warning("[NOTIFY:TELEGRAM] ❌ Webhook connection failed.")
    except requests.exceptions.HTTPError as e:
        logger.warning(
            f"[NOTIFY:TELEGRAM] ❌ Webhook returned error status: {e.response.status_code}"
        )
    except Exception as e:
        logger.warning(f"[NOTIFY:TELEGRAM] ❌ Webhook dispatch failed: {e}")

    return False


class TelegramNotificationChannel(NotificationChannel):

    # ── Job Failure ────────────────────────────────────────────────────────────

    def send_job_failure(
        self,
        job_id: str,
        job_name: str,
        error: str,
        tb: str,
        retry_count: int,
    ) -> None:
        tb_snippet = (tb or "")[:500]
        _post(
            {
                **_build_base("job_failure"),
                "event": "job_failure",
                "job_id": job_id,
                "job_name": job_name,
                "retry_count": retry_count,
                "error": error,
                "traceback": tb_snippet,
                "message": (
                    f"🚨 Scheduler job *{job_name}* failed (retry #{retry_count})\n"
                    f"Error: {error}"
                ),
            }
        )

    # ── Connection Accepted ────────────────────────────────────────────────────

    def send_connection_accepted(
        self,
        contact_name: str,
        profile_url: str,
        company_name: str,
        profile_name: str = "",
    ) -> None:
        _post(
            {
                **_build_base("connection_accepted"),
                "event": "connection_accepted",
                "action_id_name": profile_name,  # which LinkedIn account took the action
                "contact_name": contact_name,
                "contact_company": company_name,
                "contact_profile_url": profile_url,
                "message": (
                    f"🤝 LinkedIn connection accepted: *{contact_name}* ({company_name})\n"
                    f"Profile: {profile_url}"
                    + (f"\nAccount: {profile_name}" if profile_name else "")
                ),
            }
        )

    # ── Reply Received ─────────────────────────────────────────────────────────

    def send_reply_received(
        self,
        contact_name: str,
        company_name: str,
        reply_snippet: str,
    ) -> None:
        snippet = (reply_snippet or "")[:300]
        _post(
            {
                **_build_base("reply_received"),
                "event"         : "reply_received",
                "contact_name"  : contact_name,
                "contact_company": company_name,
                "reply_snippet" : snippet,
                "message"       : (
                    f"💬 Reply from *{contact_name}* ({company_name}):\n{snippet}"
                ),
            }
        )
