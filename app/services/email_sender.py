from __future__ import annotations
from app.utils.logger import logger
import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def _get_smtp_config() -> dict:
    return {
        "host"    : os.getenv("EMAIL_HOST",          "sandbox.smtp.mailtrap.io"),
        "port"    : int(os.getenv("EMAIL_PORT",      "2525")),
        "user"    : os.getenv("EMAIL_HOST_USER",     ""),
        "password": os.getenv("EMAIL_HOST_PASSWORD", ""),
        "from"    : os.getenv("EMAIL_FROM",          "outreach@example.com"),
    }


def send_pitch_email(
    to_address : str,
    subject    : str,
    body       : str,
    *,
    html       : bool = False,
) -> bool:
    cfg = _get_smtp_config()

    if not cfg["user"] or not cfg["password"]:
        raise RuntimeError(
            "[EMAIL] SMTP credentials not configured. "
            "Set EMAIL_HOST_USER and EMAIL_HOST_PASSWORD in .env"
        )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject or "(no subject)"
    msg["From"]    = cfg["from"]
    msg["To"]      = to_address

    mime_type = "html" if html else "plain"
    msg.attach(MIMEText(body, mime_type, "utf-8"))

    port = cfg["port"]

    try:
        if port == 465:
            # SMTP over SSL
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(cfg["host"], port, context=context) as server:
                server.login(cfg["user"], cfg["password"])
                server.sendmail(cfg["from"], to_address, msg.as_string())
        else:
            # Plain SMTP with optional STARTTLS (ports 587, 2525, 25)
            with smtplib.SMTP(cfg["host"], port, timeout=15) as server:
                server.ehlo()
                try:
                    server.starttls()
                    server.ehlo()
                except smtplib.SMTPNotSupportedError:
                    pass  # server doesn't support STARTTLS — continue plaintext
                server.login(cfg["user"], cfg["password"])
                server.sendmail(cfg["from"], to_address, msg.as_string())

        logger.info(f"[EMAIL] ✅ Sent to {to_address} | subject: {subject!r}")
        return True

    except smtplib.SMTPAuthenticationError as exc:
        raise RuntimeError(f"[EMAIL] Authentication failed: {exc}") from exc

    except smtplib.SMTPRecipientsRefused as exc:
        raise RuntimeError(f"[EMAIL] Recipient refused: {to_address} — {exc}") from exc

    except Exception as exc:
        raise RuntimeError(f"[EMAIL] Unexpected SMTP error: {exc}") from exc
