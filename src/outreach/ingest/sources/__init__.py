"""Pluggable ingest sources."""

from __future__ import annotations

from outreach.config import Settings
from outreach.protocols import Source


def get_source(settings: Settings) -> Source:
    name = settings.config.ingest.source.lower().strip()
    if name == "apify":
        from outreach.ingest.sources.apify import ApifyLinkedInSource

        return ApifyLinkedInSource(settings)
    if name == "csv":
        from outreach.ingest.sources.csv_source import CsvSource

        return CsvSource(settings)
    if name in {"linkedin_feed", "feed", "linkedin"}:
        from outreach.ingest.sources.linkedin_feed import LinkedInFeedSource

        return LinkedInFeedSource(settings)
    if name in {"linkedin_dms", "dms", "messaging", "linkedin_messages"}:
        from outreach.ingest.sources.linkedin_dms import LinkedInDmsSource

        return LinkedInDmsSource(settings)
    raise ValueError(
        f"Unknown ingest.source: {name!r} "
        "(expected apify|csv|linkedin_feed|linkedin_dms)"
    )
