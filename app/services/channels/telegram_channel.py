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


def _post(payload: dict) -> tuple[bool, dict | None]:
    """POST a JSON payload to the webhook URL with Bearer token auth. Returns (success, response_json)."""
    webhook_url = os.getenv("WEBHOOK_URL", "").strip()
    webhook_token = os.getenv("WEBHOOK_TOKEN", "").strip()

    if not webhook_url:
        logger.warning("[NOTIFY:TELEGRAM] WEBHOOK_URL not set — notification skipped.")
        return True, None  # not a failure, just unconfigured

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if webhook_token:
        headers["Authorization"] = f"Bearer {webhook_token}"

    try:
        response = requests.post(webhook_url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        logger.info("[NOTIFY:TELEGRAM] ✅ Webhook alert sent.")
        resp_json = None
        try:
            resp_json = response.json()
        except Exception:
            pass
        return True, resp_json

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

    return False, None


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
        url_str = str(profile_url or "").strip()
        _post(
            {
                **_build_base("connection_accepted"),
                "event": "connection_accepted",
                "action_id_name": profile_name,  # which LinkedIn account took the action
                "contact_name": contact_name,
                "contact_company": company_name,
                "contact_profile_url": url_str,
                "message": (
                    f"🤝 LinkedIn connection accepted: *{contact_name}* ({company_name})\n"
                    f"{url_str}"
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
        profile_id: int | None = None,
        suggested_reply: str | None = None,
        sales_pitch_id: int | None = None,
    ) -> str | None:
        snippet = (reply_snippet or "")[:500]

        payload = {
            **_build_base("reply_received"),
            "event"           : "reply_received",
            "profile_id"      : profile_id,
            "sales_pitch_id"  : sales_pitch_id,
            "contact_name"    : contact_name,
            "contact_company" : company_name,
            "reply_snippet"   : snippet,
            "reply_suggestion" : suggested_reply or "",
        }

        # logger.info(f"[NOTIFY:TELEGRAM] 💬 Sending reply received notification to Telegram. Payload: {payload}")

        success, resp_json = _post(payload)

        # logger.info(f"[NOTIFY:TELEGRAM] 💬 Reply received notification sent successfully. Response: {resp_json}")
        telegram_message_id = None
        if success and resp_json and isinstance(resp_json, dict):
            data_list = resp_json.get("data")
            if isinstance(data_list, list) and len(data_list) > 0 and isinstance(data_list[0], dict):
                try:
                    message_id = data_list[0]["data"]["result"]["message_id"]
                    telegram_message_id = str(message_id) if message_id is not None else None
                except (KeyError, TypeError):
                    logger.warning("[NOTIFY:TELEGRAM] ⚠️ Could not extract message_id from webhook response.")

        return telegram_message_id

