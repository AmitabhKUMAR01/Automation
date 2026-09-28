"""Helpers for LinkedIn feed/DM hiring-post detection (no browser)."""

from __future__ import annotations

import re

_HIRING_HINTS = re.compile(
    r"("
    r"\bhiring\b|"
    r"\bwe(?:'re| are) (?:hiring|looking for)\b|"
    r"\bnow hiring\b|"
    r"\bopenings?\b|"
    r"\bopen role\b|"
    r"\bjob opening\b|"
    r"\bjoin (?:our|my) team\b|"
    r"\blooking for (?:a |an )?(?:developer|engineer|full[\s-]?stack)\b|"
    r"\bneeds? (?:a |an )?(?:\w+\s+){0,3}(?:developer|engineer)\b|"
    r"\bimmediately hiring\b|"
    r"\b#hiring\b|"
    r"\breferral\b|"
    r"\bapply (?:here|now|via|at)\b|"
    r"\binterested in joining\b|"
    r"\bjd\b|"
    r"\bjob desc"
    r")",
    re.IGNORECASE,
)

_JOB_URL = re.compile(
    r"("
    r"linkedin\.com/jobs|"
    r"linkedin\.com/hiring|"
    r"indeed\.com|"
    r"naukri\.com|"
    r"greenhouse\.io|"
    r"boards\.greenhouse|"
    r"jobs\.lever\.co|"
    r"lever\.co|"
    r"wellfound\.com|"
    r"angel\.co|"
    r"workday\.com|"
    r"myworkdayjobs\.com|"
    r"ashbyhq\.com|"
    r"jobs\.ashby|"
    r"smartrecruiters\.com|"
    r"icims\.com|"
    r"jobvite\.com"
    r")",
    re.IGNORECASE,
)

# Generic hiring phrases that must NOT count as a role match by themselves
_GENERIC_NEEDLES = frozenset(
    {
        "hiring",
        "we're hiring",
        "we are looking for",
        "looking for",
        "open role",
        "openings",
        "job opening",
        "now hiring",
    }
)


def build_feed_needles(search_terms: list[str], target_roles: list[str]) -> list[str]:
    needles = [t.strip().lower() for t in search_terms if t.strip()]
    needles.extend(r.strip().lower() for r in target_roles if r.strip())
    for extra in ("hiring", "we're hiring", "looking for"):
        if extra not in needles:
            needles.append(extra)
    return needles


def role_needles_from(needles: list[str]) -> list[str]:
    """Role/tech needles only — excludes generic 'hiring' phrases."""
    return [n for n in needles if n not in _GENERIC_NEEDLES and len(n) >= 4]


def has_job_url(text: str) -> bool:
    return bool(text and _JOB_URL.search(text))


def looks_like_hiring_post(
    text: str,
    needles: list[str],
    *,
    require_role: bool = True,
) -> bool:
    """
    True if post looks like a hiring item worth ingesting.

    For LinkedIn feed, require_role=True so Customer Support / Marketing
    '#hiring' noise is dropped unless it also mentions a target role
    (full stack, react, node, etc.).
    """
    if not text or len(text.strip()) < 25:
        return False

    lowered = text.lower()
    has_hire = bool(_HIRING_HINTS.search(text)) or has_job_url(text)
    roles = role_needles_from(needles)
    has_role = any(n in lowered for n in roles) if roles else True

    if require_role:
        # Must look like hiring AND mention a target role/stack
        if has_hire and has_role:
            return True
        # Strong role mention + apply/JD language even without generic 'hiring'
        if has_role and (
            "apply" in lowered
            or "jd" in lowered
            or "job desc" in lowered
            or has_job_url(text)
        ):
            return True
        return False

    # DM / loose mode: hiring signal or role needle alone can qualify
    if has_hire:
        return True
    if len(text.strip()) < 40:
        return False
    return any(n in lowered for n in needles if len(n) >= 4)
