"""Business-hours / daily / cooldown send guards."""

from __future__ import annotations

import random
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlmodel import func, select

from outreach.config import Settings
from outreach.db.models import RecipientCooldown, SendLog, utcnow
from outreach.db.session import session_scope


def normalize_email(email: str) -> str:
    return email.strip().lower()


def now_in_tz(tz_name: str) -> datetime:
    return datetime.now(ZoneInfo(tz_name))


def within_business_hours(settings: Settings, when: datetime | None = None) -> tuple[bool, str]:
    cfg = settings.config.send
    local = when or now_in_tz(cfg.timezone)
    if cfg.weekdays_only and local.weekday() >= 5:
        return False, "weekend"
    start_h, start_m = map(int, cfg.business_hours.start.split(":"))
    end_h, end_m = map(int, cfg.business_hours.end.split(":"))
    start_t = time(start_h, start_m)
    end_t = time(end_h, end_m)
    if not (start_t <= local.time() <= end_t):
        return False, f"outside business hours ({cfg.business_hours.start}-{cfg.business_hours.end} {cfg.timezone})"
    return True, "ok"


def sends_today(settings: Settings) -> int:
    cfg = settings.config.send
    local = now_in_tz(cfg.timezone)
    start_local = datetime.combine(local.date(), time(0, 0), tzinfo=ZoneInfo(cfg.timezone))
    start_utc = start_local.astimezone(timezone.utc)
    with session_scope() as session:
        count = session.exec(
            select(func.count()).select_from(SendLog).where(SendLog.sent_at >= start_utc)
        ).one()
        return int(count or 0)


def purge_expired_cooldowns(settings: Settings) -> int:
    days = settings.config.send.email_cooldown_days
    cutoff = utcnow() - timedelta(days=days)
    deleted = 0
    with session_scope() as session:
        rows = list(
            session.exec(select(RecipientCooldown).where(RecipientCooldown.sent_at < cutoff)).all()
        )
        for row in rows:
            session.delete(row)
            deleted += 1
    return deleted


def cooldown_blocks(email: str) -> bool:
    """True if UNIQUE cooldown row still exists for this address."""
    key = normalize_email(email)
    with session_scope() as session:
        row = session.exec(
            select(RecipientCooldown).where(RecipientCooldown.email_normalized == key)
        ).first()
        return row is not None


def random_send_delay_seconds(settings: Settings) -> float:
    cfg = settings.config.send
    lo = min(cfg.delay_seconds_min, cfg.delay_seconds_max)
    hi = max(cfg.delay_seconds_min, cfg.delay_seconds_max)
    return float(random.uniform(lo, hi))
