"""Who is worth a job-ask message. Pure functions — no browser, easy to test."""

from __future__ import annotations

import re
from datetime import date, datetime

_HR = re.compile(
    r"\b(hr|hrbp|recruit\w*|talent|human resources?|people (?:ops|operations|partner)|"
    r"hiring|staffing|acquisition)\b",
    re.IGNORECASE,
)
_LEADER = re.compile(
    r"\b(ceo|cto|coo|cfo|cpo|founder|co-?founder|director|vp|vice president|head of|"
    r"president|owner|managing partner|chief)\b",
    re.IGNORECASE,
)
_SENIOR = re.compile(
    r"\b(manager|lead|senior|sr\.?|staff|principal|architect|engineering manager|em)\b",
    re.IGNORECASE,
)

_MONTHS = {
    m: i
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"],
        start=1,
    )
}
_CONNECTED_ON = re.compile(r"connected on\s+([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", re.IGNORECASE)
_CONNECTED_AGO = re.compile(
    r"connected\s+(\d+|an?|one)\s+(minute|hour|day|week|month|year)s?\s+ago", re.IGNORECASE
)
_YEAR = re.compile(r"\b(19[89]\d|20[0-4]\d)\b")
_EMOJI_AND_SYMBOLS = re.compile(r"[^\w\s'.-]", re.UNICODE)
_TITLES = re.compile(r"^(dr|mr|mrs|ms|er|ca|adv|prof)\.?$", re.IGNORECASE)


def compact(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def mentions_excluded_company(text: str | None, excluded: list[str]) -> bool:
    haystack = compact(text)
    return any(compact(name) and compact(name) in haystack for name in excluded)


def mentions_company_word(text: str | None, names: list[str]) -> str | None:
    """Whole-word company match ('IBM' must not hit 'NIBM'); returns the matched name."""
    lowered = " ".join((text or "").lower().split())
    for name in names:
        n = " ".join(name.lower().split())
        if n and re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", lowered):
            return name
    return None


def categorize(headline: str | None) -> str | None:
    """hr | leader | senior from the headline, or None when it says nothing useful."""
    if not headline:
        return None
    if _HR.search(headline):
        return "hr"
    if _LEADER.search(headline):
        return "leader"
    if _SENIOR.search(headline):
        return "senior"
    return None


def first_name(full_name: str) -> str:
    cleaned = _EMOJI_AND_SYMBOLS.sub(" ", full_name or "").split()
    parts = [p for p in cleaned if not _TITLES.match(p)]
    if not parts:
        return "there"
    name = parts[0].strip(".-'")
    return name[:1].upper() + name[1:] if name else "there"


def connected_days_ago(text: str | None, today: date | None = None) -> int | None:
    if not text:
        return None
    today = today or date.today()
    m = _CONNECTED_ON.search(text)
    if m:
        month = _MONTHS.get(m.group(1)[:3].lower())
        if month:
            try:
                return (today - date(int(m.group(3)), month, int(m.group(2)))).days
            except ValueError:
                return None
    m = _CONNECTED_AGO.search(text)
    if m:
        raw = m.group(1).lower()
        n = 1 if raw in {"a", "an", "one"} else int(raw)
        unit = m.group(2).lower()
        return {"minute": 0, "hour": 0, "day": n, "week": n * 7, "month": n * 30, "year": n * 365}[unit]
    return None


def years_from_experience(items: list[str], today: date | None = None) -> int | None:
    """Career length from the earliest year on the experience page."""
    years = [int(y) for text in items for y in _YEAR.findall(text)]
    if not years:
        return None
    current = (today or datetime.now().date()).year
    return max(0, current - min(years))


def current_positions(items: list[str]) -> list[str]:
    return [t for t in items if "present" in t.lower()]


def company_from_position(text: str) -> str | None:
    """Second line of a position block is usually 'Company · Full-time'."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    company = lines[1].split("·")[0].strip()
    return company or None


def decide(
    *,
    headline: str | None,
    category: str | None,
    years: int | None,
    current_texts: list[str],
    excluded_companies: list[str],
    min_years: int,
    profile_checked: bool,
) -> tuple[bool, str | None, str]:
    """(eligible, final_category, reason)."""
    if mentions_excluded_company(headline, excluded_companies):
        return False, category, "excluded company in headline"
    if any(mentions_excluded_company(t, excluded_companies) for t in current_texts):
        return False, category, "currently at excluded company"
    if category in {"hr", "leader"}:
        return True, category, "title match"
    if years is not None and years >= min_years:
        return True, category or "experienced", f"{years}+ years experience"
    if category == "senior" and years is None:
        return True, category, "senior title (years unknown)"
    if not profile_checked:
        return False, category, "needs profile check"
    return False, category, f"under {min_years} years / not a target title"
