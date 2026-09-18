"""Enrich domain helpers."""

from outreach.enrich.domain import domain_from_text, normalize_domain


def test_domain_from_text_skips_linkedin() -> None:
    text = "Apply at https://www.linkedin.com/jobs and visit https://acme.dev/careers"
    assert domain_from_text(text) == "acme.dev"


def test_normalize_domain() -> None:
    assert normalize_domain("https://www.Example.com/path") == "example.com"
