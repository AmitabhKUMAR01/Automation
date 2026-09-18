"""Ingest dedupe-on-URL and CSV source tests."""

from __future__ import annotations

from pathlib import Path

from sqlmodel import select

from outreach.config import reload_settings
from outreach.db.models import Post, PostStatus
from outreach.db.session import init_db, reset_engine, session_scope
from outreach.ingest.sources.apify import build_keyword, item_to_raw_post
from outreach.ingest.sources.csv_source import CsvSource
from outreach.ingest.stage import persist_new_posts
from outreach.protocols import RawPost


def _isolate_db(tmp_path: Path, monkeypatch) -> None:
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    reset_engine()
    reload_settings()
    init_db()


def test_build_keyword_includes_roles() -> None:
    kw = build_keyword(["hiring", "we're hiring"], ["backend engineer", "python developer"])
    assert "hiring" in kw
    assert '"backend engineer"' in kw
    assert "OR" in kw


def test_item_to_raw_post_maps_common_shapes() -> None:
    raw = item_to_raw_post(
        {
            "postUrl": "https://www.linkedin.com/feed/update/urn:li:activity:9",
            "text": "We are hiring!",
            "authorName": "Alex",
            "authorProfileUrl": "https://www.linkedin.com/in/alex",
            "postedAt": "2026-09-17T10:00:00Z",
        }
    )
    assert raw is not None
    assert raw.url.endswith("activity:9")
    assert raw.author_name == "Alex"
    assert raw.posted_at is not None


def test_dedupe_never_reinserts_same_url(tmp_path: Path, monkeypatch) -> None:
    _isolate_db(tmp_path, monkeypatch)
    raw = [
        RawPost(url="https://linkedin.com/posts/1", raw_text="Hiring A"),
        RawPost(url="https://linkedin.com/posts/2", raw_text="Hiring B"),
    ]
    inserted, skipped = persist_new_posts(raw)
    assert inserted == 2 and skipped == 0

    inserted2, skipped2 = persist_new_posts(raw)
    assert inserted2 == 0 and skipped2 == 2

    with session_scope() as session:
        assert len(session.exec(select(Post)).all()) == 2


def test_csv_source_loads_fixtures(tmp_path: Path, monkeypatch) -> None:
    _isolate_db(tmp_path, monkeypatch)
    settings = reload_settings()
    csv_path = Path(__file__).resolve().parents[1] / "fixtures" / "sample_posts.csv"
    source = CsvSource(settings, path=csv_path)
    posts = source.fetch()
    assert len(posts) == 3
    assert all(p.raw_text for p in posts)

    inserted, skipped = persist_new_posts(posts)
    assert inserted == 3 and skipped == 0

    # second ingest of same CSV is fully deduped
    inserted2, skipped2 = persist_new_posts(posts)
    assert inserted2 == 0 and skipped2 == 3

    with session_scope() as session:
        statuses = {p.status for p in session.exec(select(Post)).all()}
        assert statuses == {PostStatus.ingested}
