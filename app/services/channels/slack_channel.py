from __future__ import annotations
import json
import os
import urllib.request
import urllib.error
from app.utils.logger import logger
from .base import NotificationChannel


def _webhook_url() -> str:
    return os.getenv("SLACK_WEBHOOK_URL", "").strip()


def _post(payload: dict) -> None:
    """POST a JSON payload to the Slack Incoming Webhook URL."""
    url = _webhook_url()
    if not url:
        logger.warning("[NOTIFY:SLACK] SLACK_WEBHOOK_URL not set — notification skipped.")
        return

    data = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(
        url,
        data    = data,
        headers = {"Content-Type": "application/json"},
        method  = "POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                logger.info("[NOTIFY:SLACK] ✅ Slack alert sent.")
            else:
                logger.warning(f"[NOTIFY:SLACK] Unexpected response: {resp.status}")
    except urllib.error.URLError as exc:
        logger.error(f"[NOTIFY:SLACK] ❌ Failed to send Slack alert: {exc}")


class SlackNotificationChannel(NotificationChannel):
    
    def send_job_failure(
        self,
        job_id: str,
        job_name: str,
        error: str,
        tb: str,
        retry_count: int,
    ) -> None:
        tb_snippet = (tb or "")[:500]
        _post({
            "text": f":rotating_light: Scheduler job *{job_name}* failed (retry #{retry_count})",
            "blocks": [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": f"🚨 Job Failed: {job_name}"},
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*Job ID:*\n`{job_id}`"},
                        {"type": "mrkdwn", "text": f"*Retry Count:*\n{retry_count}"},
                        {"type": "mrkdwn", "text": f"*Error:*\n```{error}```"},
                    ],
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*Traceback (last 500 chars):*\n```{tb_snippet}```"},
                },
                {"type": "divider"},
            ],
        })

    # ── Connection Accepted ────────────────────────────────────────────────────

    def send_connection_accepted(
        self,
        contact_name: str,
        profile_url: str,
        company_name: str,
    ) -> None:
        _post({
            "text": f":handshake: LinkedIn connection accepted: *{contact_name}* ({company_name})",
            "blocks": [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": f"🤝 Connection Accepted: {contact_name}"},
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*Contact:*\n{contact_name}"},
                        {"type": "mrkdwn", "text": f"*Company:*\n{company_name}"},
                        {"type": "mrkdwn", "text": f"*Profile:*\n<{profile_url}|View on LinkedIn>"},
                    ],
                },
                {
                    "type": "context",
                    "elements": [{"type": "mrkdwn", "text": "Consider sending your pitch now."}],
                },
                {"type": "divider"},
            ],
        })

    # ── Reply Received ─────────────────────────────────────────────────────────

    def send_reply_received(
        self,
        contact_name: str,
        company_name: str,
        reply_snippet: str,
    ) -> None:
        snippet = (reply_snippet or "")[:300]
        _post({
            "text": f":speech_balloon: Reply from *{contact_name}* ({company_name})",
            "blocks": [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": f"💬 Reply Received: {contact_name}"},
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*Contact:*\n{contact_name}"},
                        {"type": "mrkdwn", "text": f"*Company:*\n{company_name}"},
                    ],
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*Message snippet:*\n> {snippet}"},
                },
                {
                    "type": "context",
                    "elements": [{"type": "mrkdwn", "text": "Log in to LinkedIn to view the full conversation."}],
                },
                {"type": "divider"},
            ],
        })
