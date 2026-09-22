"""Build a human-readable job shortlist (alerts) — never auto-applies."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlmodel import col, select

from outreach.config import get_settings
from outreach.db.models import Post, PostStatus
from outreach.db.session import session_scope
from outreach.logging import get_logger
from outreach.stages import StageResult

log = get_logger("shortlist")

_SHORTLIST_STATUSES = (
    PostStatus.matched,
    PostStatus.pending_review,
    PostStatus.approved,
    PostStatus.needs_enrichment,
    PostStatus.parsed,
    PostStatus.enriched,
)


@dataclass(frozen=True)
class _JobRow:
    id: int
    url: str
    source: str
    status: str
    match_score: int | None
    role_title: str | None
    company_name: str | None
    author_name: str | None
    location: str | None
    raw_text: str


def _snapshot(p: Post) -> _JobRow | None:
    if p.id is None:
        return None
    status = p.status.value if hasattr(p.status, "value") else str(p.status)
    return _JobRow(
        id=p.id,
        url=p.url,
        source=p.source,
        status=status,
        match_score=p.match_score,
        role_title=p.role_title,
        company_name=p.company_name,
        author_name=p.author_name,
        location=p.location,
        raw_text=p.raw_text or "",
    )


def run(
    *,
    source: str | None = "naukri",
    min_score: int | None = None,
    limit: int = 50,
    dry_run: bool = False,
) -> StageResult:
    """
    Write matched / promising jobs to a markdown shortlist for manual apply.

    Default focuses on Naukri discoveries. Pass source=None for all sources.
    """
    settings = get_settings()
    cutoff = min_score if min_score is not None else settings.config.match.score_cutoff
    out_path = settings.resolve_path(settings.config.ingest.naukri.shortlist_path)

    with session_scope() as session:
        q = select(Post).where(col(Post.status).in_(_SHORTLIST_STATUSES))
        if source:
            q = q.where(Post.source == source)
        orm_rows = list(
            session.exec(q.order_by(Post.ingested_at.desc()).limit(limit * 3)).all()
        )
        # Snapshot while session is open (avoid DetachedInstanceError)
        rows = [s for s in (_snapshot(p) for p in orm_rows) if s is not None]

    scored = [p for p in rows if p.match_score is not None and (p.match_score or 0) >= cutoff]
    unscored = [p for p in rows if p.match_score is None]
    near = [
        p
        for p in rows
        if p.match_score is not None and cutoff - 10 <= (p.match_score or 0) < cutoff
    ]
    selected: list[_JobRow] = []
    seen: set[int] = set()
    for group in (scored, near, unscored):
        for p in group:
            if p.id in seen:
                continue
            seen.add(p.id)
            selected.append(p)
            if len(selected) >= limit:
                break
        if len(selected) >= limit:
            break

    lines = [
        f"# Job shortlist ({source or 'all sources'})",
        "",
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"Min score for primary list: {cutoff}",
        "",
        "> **Manual apply only.** This file is an alert list — the pipeline never",
        "> submits applications on Naukri or elsewhere.",
        "",
    ]

    if not selected:
        lines.append("_No jobs to shortlist yet. Run ingest → parse → match first._")
        lines.append("")
    else:
        lines.append(f"## Primary matches (score ≥ {cutoff}) — {len(scored)}")
        lines.append("")
        for i, p in enumerate(scored[:limit], 1):
            lines.extend(_format_job(i, p))

        if near:
            lines.append(f"## Near misses (score {cutoff - 10}–{cutoff - 1}) — {len(near)}")
            lines.append("")
            for i, p in enumerate(near[: max(0, limit - len(scored))], 1):
                lines.extend(_format_job(i, p))

        remaining = limit - len(scored) - len(near)
        if unscored and remaining > 0:
            lines.append(f"## Not scored yet — {min(len(unscored), remaining)}")
            lines.append("")
            lines.append("_Run `uv run pipeline match` to score these._")
            lines.append("")
            for i, p in enumerate(unscored[:remaining], 1):
                lines.extend(_format_job(i, p))

    text = "\n".join(lines) + "\n"
    details = [
        f"source={source or 'all'}",
        f"min_score={cutoff}",
        f"jobs={len(selected)}",
        f"path={out_path}",
        "note=manual_apply_only",
    ]

    if dry_run:
        details.append("dry-run: shortlist not written")
        return StageResult(stage="shortlist", dry_run=True, details=details)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    log.info("shortlist_written", path=str(out_path), jobs=len(selected))

    return StageResult(
        stage="shortlist",
        dry_run=False,
        processed=len(selected),
        succeeded=len(selected),
        details=details,
    )


def _format_job(index: int, p: _JobRow) -> list[str]:
    title = p.role_title or _guess_title(p.raw_text) or "(untitled)"
    company = p.company_name or p.author_name or "?"
    score = p.match_score if p.match_score is not None else "—"
    loc = p.location or "—"
    return [
        f"### {index}. {title}",
        f"- Company: {company}",
        f"- Location: {loc}",
        f"- Score: {score} · status: {p.status}",
        f"- Source: {p.source}",
        f"- **Apply / open:** {p.url}",
        "",
    ]


def _guess_title(raw: str | None) -> str | None:
    if not raw:
        return None
    for line in raw.splitlines():
        if line.lower().startswith("role:"):
            return line.split(":", 1)[1].strip() or None
    return None
