"""Ingest stage: fetch via Source protocol, dedupe on URL, write to SQLite."""

from __future__ import annotations

from sqlmodel import select

from outreach.config import get_settings
from outreach.db.models import Post, PostStatus, utcnow
from outreach.db.session import session_scope
from outreach.ingest.sources import get_source
from outreach.logging import get_logger
from outreach.protocols import RawPost
from outreach.stages import StageResult

log = get_logger("ingest")


def existing_urls(urls: list[str]) -> set[str]:
    if not urls:
        return set()
    with session_scope() as session:
        rows = session.exec(select(Post.url).where(Post.url.in_(urls))).all()
        return set(rows)


def persist_new_posts(raw_posts: list[RawPost]) -> tuple[int, int]:
    """Insert posts whose URL is not already in the DB. Returns (inserted, skipped)."""
    urls = [p.url for p in raw_posts]
    known = existing_urls(urls)
    inserted = 0
    skipped = 0
    with session_scope() as session:
        for raw in raw_posts:
            if raw.url in known:
                skipped += 1
                continue
            session.add(
                Post(
                    url=raw.url,
                    source=raw.source,
                    author_name=raw.author_name,
                    author_profile_url=raw.author_profile_url,
                    raw_text=raw.raw_text,
                    posted_at=raw.posted_at,
                    status=PostStatus.ingested,
                    updated_at=utcnow(),
                )
            )
            known.add(raw.url)
            inserted += 1
    return inserted, skipped


def run(*, dry_run: bool = False) -> StageResult:
    settings = get_settings()
    source = get_source(settings)
    details: list[str] = [f"source={source.name}"]

    if source.name == "apify":
        from outreach.ingest.sources.apify import build_keyword

        keyword = build_keyword(
            settings.config.ingest.search_terms,
            settings.config.ingest.target_roles,
        )
        details.append(f"keyword={keyword!r}")
        details.append(f"lookback_days={settings.config.ingest.lookback_days}")
        details.append(f"max_posts={settings.config.ingest.max_posts_per_run}")
        details.append(f"actor={settings.secrets.apify_actor_id}")
    elif source.name == "linkedin_feed":
        cfg = settings.config.ingest
        details.append(f"max_posts={cfg.max_posts_per_run}")
        details.append(f"max_scrolls={cfg.feed_max_scrolls}")
        details.append(f"storage={cfg.linkedin_storage_state}")
        details.append(f"headless={cfg.linkedin_headless}")
    elif source.name == "linkedin_dms":
        cfg = settings.config.ingest
        dm = cfg.dm
        details.append(f"max_posts={cfg.max_posts_per_run}")
        details.append(f"bookmark={dm.bookmark_contacts}")
        details.append(f"inbound={dm.inbound_contacts or 'recent_hiring_previews'}")
        details.append(f"max_conversations={dm.max_conversations}")
        details.append(f"storage={cfg.linkedin_storage_state}")
        details.append(f"headless={cfg.linkedin_headless}")
        details.append("note=opening_chat_does_not_mean_applied")

    if dry_run:
        details.append("dry-run: no external fetch, no DB writes")
        return StageResult(stage="ingest", dry_run=True, details=details)

    try:
        raw_posts = source.fetch()
    except Exception as exc:
        log.exception("ingest_failed", error=str(exc))
        return StageResult(
            stage="ingest",
            dry_run=False,
            failed=1,
            details=details + [f"error: {exc}"],
        )

    inserted, skipped = persist_new_posts(raw_posts)
    details.append(f"fetched={len(raw_posts)} inserted={inserted} deduped={skipped}")
    log.info("ingest_done", fetched=len(raw_posts), inserted=inserted, skipped=skipped)
    return StageResult(
        stage="ingest",
        dry_run=False,
        processed=len(raw_posts),
        succeeded=inserted,
        skipped=skipped,
        details=details,
    )
