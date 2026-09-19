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
    r"\blooking for (?:a |an )?(?:developer|engineer)\b|"
    r"\bimmediately hiring\b|"
    r"\b#hiring\b|"
    r"\breferral\b|"
    r"\bapply (?:here|now|via|at)\b|"
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


def build_feed_needles(search_terms: list[str], target_roles: list[str]) -> list[str]:
    needles = [t.strip().lower() for t in search_terms if t.strip()]
    needles.extend(r.strip().lower() for r in target_roles if r.strip())
    # Always keep core hiring signals
    for extra in ("hiring", "we're hiring", "looking for"):
        if extra not in needles:
            needles.append(extra)
    return needles


def has_job_url(text: str) -> bool:
    return bool(text and _JOB_URL.search(text))


def looks_like_hiring_post(text: str, needles: list[str]) -> bool:
    """True if post/DM text looks like a hiring/outreach item worth ingesting."""
    if not text or len(text.strip()) < 25:
        return False
    if has_job_url(text):
        return True
    if len(text.strip()) < 40 and not _HIRING_HINTS.search(text):
        return False
    lowered = text.lower()
    if _HIRING_HINTS.search(text):
        return True
    return any(n in lowered for n in needles if len(n) >= 4)
