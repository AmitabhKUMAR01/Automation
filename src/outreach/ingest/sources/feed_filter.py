"""Helpers for LinkedIn feed hiring-post detection (no browser)."""

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
    r"\b#hiring\b"
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


def looks_like_hiring_post(text: str, needles: list[str]) -> bool:
    """True if post text looks like a hiring/outreach post worth ingesting."""
    if not text or len(text.strip()) < 40:
        return False
    lowered = text.lower()
    if _HIRING_HINTS.search(text):
        return True
    return any(n in lowered for n in needles if len(n) >= 4)
