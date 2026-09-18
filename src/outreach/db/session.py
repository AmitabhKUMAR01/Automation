"""SQLite engine / session helpers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

from outreach.config import Settings, get_settings

_engine = None


def get_engine(settings: Settings | None = None):
    global _engine
    if _engine is not None:
        return _engine

    settings = settings or get_settings()
    url = settings.database_url

    if url.startswith("sqlite:///"):
        db_path = Path(url.removeprefix("sqlite:///"))
        db_path.parent.mkdir(parents=True, exist_ok=True)

    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    _engine = create_engine(url, echo=False, connect_args=connect_args)
    return _engine


def init_db(settings: Settings | None = None) -> None:
    """Create tables if missing. Safe to call on every CLI invocation."""
    # Import models so SQLModel metadata is populated.
    from outreach.db import models as _models  # noqa: F401

    engine = get_engine(settings)
    SQLModel.metadata.create_all(engine)


@contextmanager
def session_scope(settings: Settings | None = None) -> Iterator[Session]:
    init_db(settings)
    with Session(get_engine(settings)) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def reset_engine() -> None:
    """Test helper: drop cached engine."""
    global _engine
    if _engine is not None:
        _engine.dispose()
        _engine = None
