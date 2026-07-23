from __future__ import annotations
import os
import smtplib
import ssl
import textwrap
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.utils.logger import logger
from .base import NotificationChannel


def _smtp_cfg() -> dict:
    return {
        "host"    : os.getenv("EMAIL_HOST",          "sandbox.smtp.mailtrap.io"),
        "port"    : int(os.getenv("EMAIL_PORT",      "2525")),
        "user"    : os.getenv("EMAIL_HOST_USER",     ""),
        "password": os.getenv("EMAIL_HOST_PASSWORD", ""),
        "from"    : os.getenv("EMAIL_FROM",          "alerts@example.com"),
    }


def _send(subject: str, html_body: str) -> None:
    """Low-level SMTP send. Raises RuntimeError on hard failures."""
    alert_email = os.getenv("ALERT_EMAIL", "").strip()
    if not alert_email:
        logger.warning("[NOTIFY:EMAIL] ALERT_EMAIL not set — notification skipped.")
        return

    cfg = _smtp_cfg()
    if not cfg["user"] or not cfg["password"]:
        logger.warning("[NOTIFY:EMAIL] SMTP credentials not configured — notification skipped.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = cfg["from"]
    msg["To"]      = alert_email
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    port = cfg["port"]
    try:
        if port == 465:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(cfg["host"], port, context=ctx) as s:
                s.login(cfg["user"], cfg["password"])
                s.sendmail(cfg["from"], alert_email, msg.as_string())
        else:
            with smtplib.SMTP(cfg["host"], port, timeout=15) as s:
                s.ehlo()
                try:
                    s.starttls()
                    s.ehlo()
                except smtplib.SMTPNotSupportedError:
                    pass
                s.login(cfg["user"], cfg["password"])
                s.sendmail(cfg["from"], alert_email, msg.as_string())

        logger.info(f"[NOTIFY:EMAIL] ✅ Alert sent to {alert_email} | {subject!r}")

    except Exception as exc:
        logger.error(f"[NOTIFY:EMAIL] ❌ Failed to send alert: {exc}")


# ── HTML Templates ─────────────────────────────────────────────────────────────

_BASE_STYLE = """
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  max-width: 600px; margin: 0 auto; padding: 24px;
  color: #1a1a2e; background: #f4f6fb; border-radius: 8px;
"""

def _card(icon: str, title: str, color: str, rows: list[tuple[str, str]], note: str = "") -> str:
    row_html = "".join(
        f"<tr><td style='padding:6px 12px;color:#666;white-space:nowrap'><b>{k}</b></td>"
        f"<td style='padding:6px 12px;word-break:break-all'>{v}</td></tr>"
        for k, v in rows
    )
    note_html = f"<p style='margin-top:16px;font-size:13px;color:#888'>{note}</p>" if note else ""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return textwrap.dedent(f"""
    <div style="{_BASE_STYLE}">
      <div style="background:{color};color:#fff;padding:16px 20px;border-radius:6px 6px 0 0">
        <span style="font-size:22px">{icon}</span>
        <span style="font-size:18px;font-weight:600;margin-left:10px">{title}</span>
      </div>
      <div style="background:#fff;border-radius:0 0 6px 6px;padding:16px">
        <table style="border-collapse:collapse;width:100%">{row_html}</table>
        {note_html}
        <p style="margin-top:16px;font-size:12px;color:#aaa;text-align:right">{ts}</p>
      </div>
    </div>
    """)


class EmailNotificationChannel(NotificationChannel):
    """Sends HTML alert emails via SMTP using the existing email configuration."""

    # ── Job Failure ────────────────────────────────────────────────────────────

    def send_job_failure(
        self,
        job_id: str,
        job_name: str,
        error: str,
        tb: str,
        retry_count: int,
    ) -> None:
        subject = f"🚨 Scheduler Job Failed — {job_name}"
        tb_safe = tb.replace("<", "&lt;").replace(">", "&gt;") if tb else "N/A"
        rows = [
            ("Job ID",      job_id),
            ("Job Name",    job_name),
            ("Retry Count", str(retry_count)),
            ("Error",       f"<span style='color:#c0392b'>{error}</span>"),
        ]
        tb_block = (
            f"<pre style='background:#f8f8f8;padding:12px;border-radius:4px;"
            f"font-size:12px;overflow:auto;border-left:3px solid #c0392b'>{tb_safe}</pre>"
        )
        body = _card("🚨", f"Job Failed: {job_name}", "#c0392b", rows) + tb_block
        _send(subject, body)

    # ── Connection Accepted ────────────────────────────────────────────────────

    def send_connection_accepted(
        self,
        contact_name: str,
        profile_url: str,
        company_name: str,
        profile_name: str = "",
    ) -> None:
        subject = f"🤝 LinkedIn Connection Accepted — {contact_name}"
        if profile_name:
            subject += f" ({profile_name})"
        rows = []
        if profile_name:
            rows.append(("Accepted For Profile", profile_name))
        rows.extend([
            ("Contact",    contact_name),
            ("Company",    company_name),
            ("Profile",    f"<a href='{profile_url}'>{profile_url}</a>"),
        ])
        body = _card(
            "🤝", "Connection Accepted", "#27ae60", rows,
            note="This contact has accepted your LinkedIn connection request. Consider sending your pitch."
        )
        _send(subject, body)

    # ── Reply Received ─────────────────────────────────────────────────────────

    def send_reply_received(
        self,
        contact_name: str,
        company_name: str,
        reply_snippet: str,
    ) -> None:
        subject = f"💬 LinkedIn Reply Received — {contact_name}"
        snippet_safe = (reply_snippet or "")[:300].replace("<", "&lt;").replace(">", "&gt;")
        rows = [
            ("Contact", contact_name),
            ("Company", company_name),
            ("Reply",   f"<em style='color:#2c3e50'>\"{snippet_safe}...\"</em>"),
        ]
        body = _card(
            "💬", "Reply Received", "#2980b9", rows,
            note="Log in to LinkedIn to view the full conversation and respond."
        )
        _send(subject, body)
