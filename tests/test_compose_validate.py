"""Compose draft validation rules."""

from __future__ import annotations

from outreach.compose.schema import ComposeDraft
from outreach.compose.stage import validate_draft, _word_count


def test_word_count() -> None:
    assert _word_count("Hello there friend") == 3


def test_validate_rejects_em_dash_and_banned_opener() -> None:
    draft = ComposeDraft(
        subject="Role at Acme",
        body="I hope this email finds you well — I built APIs at Acme.",
        refusal=False,
        post_detail_referenced="FastAPI",
    )
    post = "We need FastAPI experience for our hiring round."
    out = validate_draft(draft, max_words=150, post_text=post)
    assert out.refusal is True
    assert out.refusal_reason
    assert "dash" in out.refusal_reason or "opener" in out.refusal_reason


def test_validate_rejects_missing_post_detail() -> None:
    draft = ComposeDraft(
        subject="Hi",
        body="I am interested in the role and have Python experience.",
        refusal=False,
        post_detail_referenced=None,
    )
    out = validate_draft(draft, max_words=150, post_text="Hiring Python folks for Redis work")
    assert out.refusal is True


def test_validate_accepts_clean_draft() -> None:
    draft = ComposeDraft(
        subject="Python Backend role",
        body="Hi, I saw you need Redis experience. I have shipped Redis-backed queues in production.",
        refusal=False,
        post_detail_referenced="Redis",
    )
    out = validate_draft(
        draft,
        max_words=150,
        post_text="Hiring backend engineer with Redis and Docker.",
    )
    assert out.refusal is False
