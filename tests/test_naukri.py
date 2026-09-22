"""Naukri discovery helpers (no browser)."""

from outreach.ingest.sources import normalize_source
from outreach.ingest.sources.naukri import build_search_url, _looks_relevant, _slug


def test_slug() -> None:
    assert _slug("Full Stack Developer") == "full-stack-developer"
    assert _slug("delhi ncr") == "delhi-ncr"


def test_build_search_url_location() -> None:
    url = build_search_url("full stack developer", "delhi-ncr")
    assert "full-stack-developer-jobs-in-delhi-ncr" in url


def test_build_search_url_remote() -> None:
    url = build_search_url("react developer", "remote")
    assert "react-developer-jobs" in url
    assert "remote" in url


def test_relevance_filter() -> None:
    assert _looks_relevant("Full Stack Developer", "React Node", ["full stack", "react"])
    assert not _looks_relevant("SAP MM Consultant", "SAP modules", ["full stack", "react"])


def test_naukri_alias() -> None:
    assert normalize_source("naukri") == "naukri"
