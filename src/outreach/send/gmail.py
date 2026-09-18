"""Gmail OAuth send helper."""

from __future__ import annotations

import base64
import mimetypes
from email.message import EmailMessage
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from outreach.config import Settings
from outreach.http_util import HttpError
from outreach.logging import get_logger

log = get_logger("send.gmail")

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


def get_gmail_credentials(settings: Settings) -> Credentials:
    secrets_path = settings.resolve_path(settings.secrets.gmail_client_secrets_path)
    token_path = settings.resolve_path(settings.secrets.gmail_token_path)
    if not secrets_path.exists():
        raise FileNotFoundError(
            f"Gmail client secrets missing: {secrets_path}. "
            "Download OAuth Desktop client JSON from Google Cloud Console."
        )

    creds: Credentials | None = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json(), encoding="utf-8")
    elif not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), SCOPES)
        creds = flow.run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json(), encoding="utf-8")

    return creds


def build_message(
    *,
    sender: str,
    to_email: str,
    subject: str,
    body: str,
    attachment_path: Path | None,
) -> dict:
    msg = EmailMessage()
    msg["To"] = to_email
    msg["From"] = sender
    msg["Subject"] = subject
    msg.set_content(body)

    if attachment_path is not None and attachment_path.exists():
        ctype, encoding = mimetypes.guess_type(str(attachment_path))
        if mime is None:
            mime = "application/octet-stream"
        maintype, subtype = mime.split("/", 1)
        raw = attachment_path.read_bytes()
        msg.add_attachment(
            raw,
            maintype=maintype,
            subtype=subtype,
            filename=attachment_path.name,
        )

    encoded = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    return {"raw": encoded}


def send_email(
    settings: Settings,
    *,
    to_email: str,
    subject: str,
    body: str,
) -> str:
    sender = settings.secrets.gmail_sender
    if not sender:
        raise RuntimeError("GMAIL_SENDER is required in .env")

    attachment = settings.resolve_path(settings.config.send.resume_attachment_path)
    if not attachment.exists():
        log.warning("resume_attachment_missing", path=str(attachment))
        attachment_path = None
    else:
        attachment_path = attachment

    creds = get_gmail_credentials(settings)
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    payload = build_message(
        sender=sender,
        to_email=to_email,
        subject=subject,
        body=body,
        attachment_path=attachment_path,
    )
    try:
        result = (
            service.users()
            .messages()
            .send(userId="me", body=payload)
            .execute()
        )
    except Exception as exc:
        raise HttpError(f"Gmail send failed: {exc}") from exc

    message_id = result.get("id") or ""
    log.info("gmail_sent", to=to_email, message_id=message_id)
    return message_id
