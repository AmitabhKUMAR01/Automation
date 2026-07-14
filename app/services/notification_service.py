from __future__ import annotations
import os
from app.utils.logger import logger
from app.services.channels.base import NotificationChannel
from app.services.channels.email_channel import EmailNotificationChannel
from app.services.channels.slack_channel import SlackNotificationChannel

_CHANNEL_REGISTRY: dict[str, type[NotificationChannel]] = {
    "email": EmailNotificationChannel,
    "slack": SlackNotificationChannel,
}

_channel_instance: NotificationChannel | None = None


def get_channel() -> NotificationChannel:
    """Return the singleton channel instance, creating it if necessary."""
    global _channel_instance
    if _channel_instance is not None:
        return _channel_instance

    name = os.getenv("NOTIFICATION_CHANNEL", "email").strip().lower()
    cls  = _CHANNEL_REGISTRY.get(name)

    if cls is None:
        logger.warning(
            f"[NOTIFY] Unknown NOTIFICATION_CHANNEL={name!r}. "
            f"Valid options: {list(_CHANNEL_REGISTRY)}. Falling back to 'email'."
        )
        cls = EmailNotificationChannel

    logger.info(f"[NOTIFY] Notification channel: {name} ({cls.__name__})")
    _channel_instance = cls()
    return _channel_instance


def notify_job_failure(
    job_id: str,
    job_name: str,
    error: str,
    tb: str,
    retry_count: int,
) -> None:
    """Send an alert when a scheduled job fails."""
    try:
        get_channel().send_job_failure(job_id, job_name, error, tb, retry_count)
    except Exception as exc:
        # Notification must NEVER crash the scheduler
        logger.error(f"[NOTIFY] ❌ Failed to send job failure notification: {exc}")


def notify_connection_accepted(
    contact_name: str,
    profile_url: str,
    company_name: str,
) -> None:
    """Send an alert when a LinkedIn connection request is accepted."""
    try:
        get_channel().send_connection_accepted(contact_name, profile_url, company_name)
    except Exception as exc:
        logger.error(f"[NOTIFY] ❌ Failed to send connection accepted notification: {exc}")


def notify_reply_received(
    contact_name: str,
    company_name: str,
    reply_snippet: str,
) -> None:
    """Send an alert when a reply to a LinkedIn pitch DM is detected."""
    try:
        get_channel().send_reply_received(contact_name, company_name, reply_snippet)
    except Exception as exc:
        logger.error(f"[NOTIFY] ❌ Failed to send reply received notification: {exc}")
