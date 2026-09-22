"""Ingest source alias resolution."""

import pytest

from outreach.ingest.sources import ALL_SOURCES, normalize_source


def test_normalize_friendly_aliases() -> None:
    assert normalize_source("feed") == "linkedin_feed"
    assert normalize_source("dms") == "linkedin_dms"
    assert normalize_source("dm") == "linkedin_dms"
    assert normalize_source("posts") == "apify"
    assert normalize_source("apify") == "apify"
    assert normalize_source("all") == "all"


def test_normalize_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="Unknown ingest source"):
        normalize_source("telegram")


def test_all_sources_order() -> None:
    assert ALL_SOURCES == ("linkedin_feed", "linkedin_dms", "apify")
