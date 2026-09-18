"""Tests for email regex extraction on messy real-world posts."""

from __future__ import annotations

from outreach.parse.emails import extract_emails
from tests.fixtures_posts import MESSY_POSTS


def test_plain_email_with_emoji_and_mail_hint() -> None:
    emails = extract_emails(MESSY_POSTS["emoji_plain_email"])
    assert emails[0] == "priya.sharma@acme.io"


def test_obfuscated_bracket_at_dot() -> None:
    emails = extract_emails(MESSY_POSTS["obfuscated_at_dot"])
    assert "jane@brightlabs.com" in emails


def test_obfuscated_parens() -> None:
    emails = extract_emails(MESSY_POSTS["obfuscated_parens"])
    assert "talent@novaapps.io" in emails


def test_no_email_returns_empty() -> None:
    assert extract_emails(MESSY_POSTS["no_email_dm_only"]) == []


def test_multiple_emails_preserves_order_unique() -> None:
    emails = extract_emails(MESSY_POSTS["multiple_emails"])
    assert emails[0] == "eng-backend@corp.example"
    assert "data-hiring@corp.example" in emails
    assert len(emails) == len(set(emails))


def test_uppercase_at_dot_words() -> None:
    emails = extract_emails(MESSY_POSTS["uppercase_at"])
    assert "hiring@megacorp.co" in emails
