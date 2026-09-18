"""Send guard helpers."""

from datetime import datetime
from zoneinfo import ZoneInfo

from outreach.config import AppConfig, Secrets, Settings
from outreach.send.guards import normalize_email, within_business_hours


def test_normalize_email() -> None:
    assert normalize_email("  Jane@X.COM ") == "jane@x.com"


def test_business_hours_weekday() -> None:
    settings = Settings(config=AppConfig(), secrets=Secrets())
    settings.config.send.timezone = "Asia/Kolkata"
    settings.config.send.weekdays_only = True
    settings.config.send.business_hours.start = "09:00"
    settings.config.send.business_hours.end = "18:00"

    monday_noon = datetime(2026, 9, 14, 12, 0, tzinfo=ZoneInfo("Asia/Kolkata"))  # Monday
    ok, _ = within_business_hours(settings, monday_noon)
    assert ok is True

    saturday = datetime(2026, 9, 19, 12, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    ok2, reason = within_business_hours(settings, saturday)
    assert ok2 is False
    assert reason == "weekend"
