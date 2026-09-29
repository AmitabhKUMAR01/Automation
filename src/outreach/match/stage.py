"""Match stage: score posts against resume; reject below cutoff."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from sqlmodel import col, select

from outreach.config import get_settings
from outreach.db.models import CompanyBlacklist, Post, PostStatus, utcnow
from outreach.db.session import session_scope
from outreach.llm import get_llm_client
from outreach.logging import get_logger
from outreach.match.resume import load_or_build_profile
from outreach.match.schema import MatchResult
from outreach.protocols import LlmMessage
from outreach.stages import StageResult

log = get_logger("match")

_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)

RESCORE_MAX_AGE_DAYS = 14


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

MATCH_SYSTEM = """You score how well a candidate fits a hiring post.
Return JSON with keys: score (0-100 integer), reasoning (string), matched_skills (array), gaps (array).
Be strict: inventing overlap is not allowed. Use only the resume facts provided.
score guide: 80-100 strong fit, 60-79 plausible, 40-59 weak, 0-39 poor/wrong role."""

_YEARS = re.compile(
    r"(\d{1,2})(?:\.\d)?\s*(?:\+|plus)?\s*(?:(?:-|–|—|to)\s*\d{1,2}\s*\+?\s*)?(?:years?|yrs?)\b",
    re.IGNORECASE,
)
_FRESHER = re.compile(r"\bfreshers?\b|\bentry[\s-]level\b|\b0\s*(?:-|to)\s*\d", re.IGNORECASE)


def min_years_required(experience_required: str | None, raw_text: str | None) -> int | None:
    """Smallest 'N years' figure in the post (structured field first), or None if absent."""
    for text in (experience_required, raw_text):
        if not text:
            continue
        found = [int(m.group(1)) for m in _YEARS.finditer(text)]
        if found:
            return min(found)
        if _FRESHER.search(text):
            return 0
    return None


def experience_policy(min_years: int | None, flex_max: int) -> str:
    if min_years is not None and min_years <= flex_max:
        return (
            f"EXPERIENCE POLICY: the post asks for about {min_years}+ years. The candidate "
            "applies to such roles regardless — do NOT lower the score for a years-of-experience "
            "gap. Score on role and tech-stack fit only."
        )
    if min_years is None:
        return (
            f"EXPERIENCE POLICY: the post states no clear years requirement. Do not penalise "
            f"experience unless it clearly demands more than {flex_max} years."
        )
    return ""


def _parse_match(content: str) -> MatchResult:
    text = content.strip()
    fence = _JSON_FENCE.search(text)
    if fence:
        text = fence.group(1).strip()
    return MatchResult.model_validate(json.loads(text))


def score_post(
    *,
    resume_blob: str,
    role_title: str | None,
    company_name: str | None,
    location: str | None,
    experience_required: str | None,
    skills: list | None,
    raw_text: str,
    client,
    temperature: float,
    flex_max_years: int = 4,
) -> MatchResult:
    policy = experience_policy(min_years_required(experience_required, raw_text), flex_max_years)
    system = f"{MATCH_SYSTEM}\n{policy}" if policy else MATCH_SYSTEM
    post_block = {
        "role_title": role_title,
        "company_name": company_name,
        "location": location,
        "experience_required": experience_required,
        "skills": skills or [],
        "post_excerpt": raw_text[:3500],
    }
    response = client.complete(
        [
            LlmMessage(role="system", content=system),
            LlmMessage(
                role="user",
                content=(
                    f"CANDIDATE RESUME FACTS:\n{resume_blob}\n\n"
                    f"HIRING POST:\n{json.dumps(post_block, ensure_ascii=False)}"
                ),
            ),
        ],
        temperature=temperature,
        response_format_json=True,
    )
    try:
        return _parse_match(response.content)
    except Exception as exc:
        # one retry
        retry = client.complete(
            [
                LlmMessage(role="system", content=system),
                LlmMessage(
                    role="user",
                    content=(
                        f"Previous JSON failed validation ({exc}). "
                        f"Return corrected JSON only.\n\n"
                        f"CANDIDATE:\n{resume_blob}\n\nPOST:\n{json.dumps(post_block)}"
                    ),
                ),
            ],
            temperature=temperature,
            response_format_json=True,
        )
        return _parse_match(retry.content)


def _blacklisted_names() -> set[str]:
    with session_scope() as session:
        rows = session.exec(select(CompanyBlacklist.company_name_normalized)).all()
        return set(rows)


def run(*, dry_run: bool = False, rescore_rejected: bool = False) -> StageResult:
    settings = get_settings()
    cutoff = settings.config.match.score_cutoff
    flex_max = settings.config.match.experience_flex_max_years

    statuses = [PostStatus.parsed, PostStatus.enriched]
    if rescore_rejected:
        statuses.append(PostStatus.rejected_match)
    with session_scope() as session:
        rows = list(
            session.exec(
                select(Post)
                .where(col(Post.status).in_(statuses))
                .where(Post.contact_email.is_not(None))  # type: ignore[union-attr]
                .order_by(Post.id)
            ).all()
        )
        # Re-score only recent rejects inside the experience window — others would fail again
        # or point at openings that have likely closed.
        fresh_after = datetime.now(timezone.utc) - timedelta(days=RESCORE_MAX_AGE_DAYS)
        rows = [
            p
            for p in rows
            if p.status != PostStatus.rejected_match
            or (
                p.ingested_at is not None
                and _aware(p.ingested_at) >= fresh_after
                and (years := min_years_required(p.experience_required, p.raw_text)) is not None
                and years <= flex_max
            )
        ]
        posts = [
            {
                "id": p.id,
                "url": p.url,
                "role_title": p.role_title,
                "company_name": p.company_name,
                "location": p.location,
                "experience_required": p.experience_required,
                "skills": p.skills,
                "raw_text": p.raw_text,
                "contact_email": p.contact_email,
            }
            for p in rows
        ]

    details = [
        f"candidates={len(posts)}",
        f"cutoff={cutoff}",
        f"experience_flex_max_years={flex_max}",
        f"rescore_rejected={rescore_rejected}",
    ]
    if not posts:
        return StageResult(stage="match", dry_run=dry_run, details=details + ["nothing to match"])

    if dry_run:
        for p in posts[:10]:
            details.append(
                f"id={p['id']} role={p['role_title']!r} email={p['contact_email']} would_score=yes"
            )
        if len(posts) > 10:
            details.append(f"... and {len(posts) - 10} more")
        details.append("dry-run: no LLM calls, no DB writes")
        return StageResult(stage="match", dry_run=True, processed=len(posts), details=details)

    profile = load_or_build_profile(settings)
    resume_blob = profile.facts_blob()
    if not profile.skills and profile.raw_text:
        resume_blob = profile.raw_text[:8000]

    blacklist = _blacklisted_names()
    client = get_llm_client(settings)
    succeeded = rejected = failed = skipped = 0

    for post in posts:
        company = (post["company_name"] or "").strip().lower()
        if company and company in blacklist:
            skipped += 1
            with session_scope() as session:
                db = session.get(Post, post["id"])
                if db:
                    db.status = PostStatus.skipped
                    db.match_reasoning = "company blacklisted"
                    db.updated_at = utcnow()
                    session.add(db)
            details.append(f"id={post['id']} skipped blacklisted company={company}")
            continue

        try:
            result = score_post(
                resume_blob=resume_blob,
                role_title=post["role_title"],
                company_name=post["company_name"],
                location=post["location"],
                experience_required=post["experience_required"],
                skills=post["skills"],
                raw_text=post["raw_text"],
                client=client,
                temperature=settings.config.llm.temperature,
                flex_max_years=flex_max,
            )
            with session_scope() as session:
                db = session.get(Post, post["id"])
                if db is None:
                    continue
                db.match_score = result.score
                db.match_reasoning = result.reasoning
                db.matched_skills = result.matched_skills
                db.skill_gaps = result.gaps
                db.updated_at = utcnow()
                if result.score >= cutoff:
                    db.status = PostStatus.matched
                    succeeded += 1
                else:
                    db.status = PostStatus.rejected_match
                    rejected += 1
                session.add(db)
            log.info(
                "match_scored",
                post_id=post["id"],
                score=result.score,
                passed=result.score >= cutoff,
                reasoning=result.reasoning[:240],
            )
        except Exception as exc:
            failed += 1
            log.exception("match_failed", post_id=post["id"], error=str(exc))
            with session_scope() as session:
                db = session.get(Post, post["id"])
                if db:
                    db.last_error = str(exc)[:2000]
                    db.updated_at = utcnow()
                    session.add(db)

    details.append(f"passed={succeeded} rejected={rejected} failed={failed} skipped={skipped}")
    return StageResult(
        stage="match",
        dry_run=False,
        processed=len(posts),
        succeeded=succeeded,
        skipped=rejected + skipped,
        failed=failed,
        details=details,
    )
