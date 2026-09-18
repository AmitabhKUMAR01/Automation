"""Smoke tests for Step 1 scaffolding."""

from __future__ import annotations

from pathlib import Path

from sqlmodel import select

from outreach.config import AppConfig, reload_settings
from outreach.db.models import Post, PostStatus
from outreach.db.session import init_db, reset_engine, session_scope


def test_config_loads_defaults(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("match:\n  score_cutoff: 72\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CONFIG_PATH", str(cfg))
    settings = reload_settings(str(cfg))
    assert settings.config.match.score_cutoff == 72
    assert settings.config.send.require_approval is True


def test_app_config_require_approval_default() -> None:
    config = AppConfig()
    assert config.send.require_approval is True
    assert config.enrich.enabled is False


def test_db_creates_posts_and_url_unique(tmp_path: Path, monkeypatch) -> None:
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    reset_engine()
    reload_settings()
    init_db()

    with session_scope() as session:
        session.add(
            Post(
                url="https://linkedin.com/feed/update/urn:1",
                raw_text="We are hiring a backend engineer.",
                status=PostStatus.ingested,
            )
        )

    with session_scope() as session:
        posts = session.exec(select(Post)).all()
        assert len(posts) == 1
        assert posts[0].status == PostStatus.ingested
