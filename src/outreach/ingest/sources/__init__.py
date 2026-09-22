"""Pluggable ingest sources."""

from __future__ import annotations

from outreach.config import Settings
from outreach.protocols import Source

# Friendly CLI / config aliases → canonical source name
SOURCE_ALIASES: dict[str, str] = {
    "feed": "linkedin_feed",
    "linkedin_feed": "linkedin_feed",
    "linkedin": "linkedin_feed",
    "dms": "linkedin_dms",
    "dm": "linkedin_dms",
    "messaging": "linkedin_dms",
    "linkedin_dms": "linkedin_dms",
    "linkedin_messages": "linkedin_dms",
    "posts": "apify",
    "apify": "apify",
    "search": "apify",
    "csv": "csv",
    "naukri": "naukri",
    "naukridotcom": "naukri",
}

# Default order when ingesting everything in one go (LinkedIn + Apify; Naukri is opt-in)
ALL_SOURCES: tuple[str, ...] = ("linkedin_feed", "linkedin_dms", "apify")


def normalize_source(name: str) -> str:
    key = name.lower().strip()
    if key in {"all", "*"}:
        return "all"
    if key not in SOURCE_ALIASES:
        allowed = "feed|dms|posts|apify|csv|naukri|linkedin_feed|linkedin_dms|all"
        raise ValueError(f"Unknown ingest source: {name!r} (expected {allowed})")
    return SOURCE_ALIASES[key]


def get_source(settings: Settings, source_name: str | None = None) -> Source:
    name = normalize_source(source_name or settings.config.ingest.source)
    if name == "all":
        raise ValueError("source 'all' is multi-source — use ingest.run(source='all')")

    if name == "apify":
        from outreach.ingest.sources.apify import ApifyLinkedInSource

        return ApifyLinkedInSource(settings)
    if name == "csv":
        from outreach.ingest.sources.csv_source import CsvSource

        return CsvSource(settings)
    if name == "linkedin_feed":
        from outreach.ingest.sources.linkedin_feed import LinkedInFeedSource

        return LinkedInFeedSource(settings)
    if name == "linkedin_dms":
        from outreach.ingest.sources.linkedin_dms import LinkedInDmsSource

        return LinkedInDmsSource(settings)
    if name == "naukri":
        from outreach.ingest.sources.naukri import NaukriSource

        return NaukriSource(settings)

    raise ValueError(f"Unhandled ingest source: {name!r}")
