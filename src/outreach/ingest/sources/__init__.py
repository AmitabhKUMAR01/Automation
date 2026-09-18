"""Pluggable ingest sources."""

from __future__ import annotations

from outreach.config import Settings
from outreach.ingest.sources.apify import ApifyLinkedInSource
from outreach.ingest.sources.csv_source import CsvSource
from outreach.protocols import Source


def get_source(settings: Settings) -> Source:
    name = settings.config.ingest.source.lower().strip()
    if name == "apify":
        return ApifyLinkedInSource(settings)
    if name == "csv":
        return CsvSource(settings)
    raise ValueError(f"Unknown ingest.source: {name!r} (expected apify|csv)")
