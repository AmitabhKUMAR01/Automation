"""Enrich stage: resolve contact email for needs_enrichment posts."""

from __future__ import annotations

from sqlmodel import select

from outreach.config import get_settings
from outreach.db.models import Post, PostStatus, utcnow
from outreach.db.session import session_scope
from outreach.enrich.domain import resolve_company_domain
from outreach.enrich.finders import get_email_finder
from outreach.logging import get_logger
from outreach.stages import StageResult

log = get_logger("enrich")


def run(*, dry_run: bool = False) -> StageResult:
    settings = get_settings()
    cfg = settings.config.enrich

    if not cfg.enabled:
        return StageResult(
            stage="enrich",
            dry_run=dry_run,
            skipped=1,
            details=["enrich.enabled=false — set true in config.yaml to run"],
        )

    with session_scope() as session:
        rows = list(
            session.exec(
                select(Post)
                .where(Post.status == PostStatus.needs_enrichment)
                .order_by(Post.id)
            ).all()
        )
        posts = [
            {
                "id": p.id,
                "company_name": p.company_name,
                "raw_text": p.raw_text,
                "role_title": p.role_title,
            }
            for p in rows
        ]

    details = [
        f"candidates={len(posts)}",
        f"provider={cfg.provider}",
        f"min_confidence={cfg.min_confidence}",
    ]
    if not posts:
        return StageResult(stage="enrich", dry_run=dry_run, details=details + ["nothing to enrich"])

    if dry_run:
        for p in posts[:10]:
            domain = resolve_company_domain(
                company_name=p["company_name"],
                post_text=p["raw_text"],
            )
            details.append(
                f"id={p['id']} company={p['company_name']!r} domain={domain or '(unknown)'} would_lookup=yes"
            )
        if len(posts) > 10:
            details.append(f"... and {len(posts) - 10} more")
        details.append("dry-run: domain resolve only, no Hunter/Apollo calls, no DB writes")
        return StageResult(stage="enrich", dry_run=True, processed=len(posts), details=details)

    finder = get_email_finder(settings)
    succeeded = skipped = failed = 0

    for post in posts:
        try:
            domain = resolve_company_domain(
                company_name=post["company_name"],
                post_text=post["raw_text"],
            )
            if not domain:
                skipped += 1
                log.info("enrich_skip_no_domain", post_id=post["id"])
                continue

            result = finder.find(
                domain=domain,
                company_name=post["company_name"],
                target_titles=cfg.target_titles,
            )
            if result is None or result.confidence < cfg.min_confidence:
                skipped += 1
                log.info(
                    "enrich_skip_low_confidence",
                    post_id=post["id"],
                    domain=domain,
                    found=bool(result),
                )
                continue

            with session_scope() as session:
                db = session.get(Post, post["id"])
                if db is None:
                    continue
                db.company_domain = domain
                db.contact_email = result.email
                db.contact_name = result.name or db.contact_name
                db.email_confidence = result.confidence
                db.application_method = db.application_method or "email"
                db.status = PostStatus.enriched
                db.updated_at = utcnow()
                session.add(db)
            succeeded += 1
            log.info(
                "enrich_ok",
                post_id=post["id"],
                email=result.email,
                confidence=result.confidence,
                provider=result.provider,
            )
        except Exception as exc:
            failed += 1
            log.exception("enrich_failed", post_id=post["id"], error=str(exc))
            with session_scope() as session:
                db = session.get(Post, post["id"])
                if db:
                    db.last_error = str(exc)[:2000]
                    db.updated_at = utcnow()
                    session.add(db)

    details.append(f"enriched={succeeded} skipped={skipped} failed={failed}")
    return StageResult(
        stage="enrich",
        dry_run=False,
        processed=len(posts),
        succeeded=succeeded,
        skipped=skipped,
        failed=failed,
        details=details,
    )
