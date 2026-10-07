"""Compose stage: personalized application email drafts."""

from __future__ import annotations

import json
import re

from sqlmodel import select

from outreach.config import get_settings
from outreach.db.models import DraftStatus, EmailDraft, Post, PostStatus, utcnow
from outreach.db.session import session_scope
from outreach.llm import LlmUnavailable, get_llm_client
from outreach.logging import get_logger
from outreach.match.resume import load_or_build_profile
from outreach.compose.schema import ComposeDraft
from outreach.protocols import LlmMessage
from outreach.send.guards import cooldown_blocks
from outreach.stages import StageResult

log = get_logger("compose")

_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_EM_DASH = re.compile(r"[\u2014\u2013]")
_BANNED_OPENER = re.compile(r"i hope this email finds you well", re.IGNORECASE)

COMPOSE_SYSTEM = """You write a short job-application email grounded ONLY in the resume facts.
Return JSON with keys:
subject (string), body (string), refusal (boolean), refusal_reason (string|null),
post_detail_referenced (string|null).

HARD RULES:
1. Reference at least one specific detail from the actual post text (not just the role title).
2. Body under MAX_WORDS words.
3. No em-dashes (—) or en-dashes (–). Use commas or periods instead.
4. Never start with or include "I hope this email finds you well".
5. Do NOT invent experience, employers, skills, or metrics absent from the resume.
6. If you cannot personalize without fabricating, set refusal=true and explain in refusal_reason.
7. Address the contact by name if available; otherwise keep greeting generic.
8. Include a clear ask to consider the candidate / schedule a chat.
"""


def _parse_draft(content: str) -> ComposeDraft:
    text = content.strip()
    fence = _JSON_FENCE.search(text)
    if fence:
        text = fence.group(1).strip()
    return ComposeDraft.model_validate(json.loads(text))


def _word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text))


def validate_draft(draft: ComposeDraft, *, max_words: int, post_text: str) -> ComposeDraft:
    """Enforce hard rules client-side; convert violations to refusal when needed."""
    if draft.refusal:
        return draft

    problems: list[str] = []
    if _EM_DASH.search(draft.body) or _EM_DASH.search(draft.subject):
        problems.append("contains em/en dash")
    if _BANNED_OPENER.search(draft.body):
        problems.append("banned opener")
    if _word_count(draft.body) > max_words:
        problems.append(f"body exceeds {max_words} words")
    detail = (draft.post_detail_referenced or "").strip()
    if not detail or detail.lower() not in post_text.lower():
        # soft check: detail should appear in post; if model paraphrases, also require non-empty
        if not detail:
            problems.append("missing post_detail_referenced")

    if problems:
        return ComposeDraft(
            subject=draft.subject or "(refused)",
            body=draft.body or "",
            refusal=True,
            refusal_reason="; ".join(problems),
            post_detail_referenced=draft.post_detail_referenced,
        )
    return draft


def generate_draft(
    *,
    resume_blob: str,
    post: dict,
    your_name: str,
    your_headline: str,
    max_words: int,
    client,
    temperature: float,
) -> ComposeDraft:
    payload = {
        "to_name": post.get("contact_name"),
        "to_email": post.get("contact_email"),
        "role_title": post.get("role_title"),
        "company_name": post.get("company_name"),
        "location": post.get("location"),
        "post_text": post.get("raw_text", "")[:4000],
        "sender_name": your_name,
        "sender_headline": your_headline,
        "MAX_WORDS": max_words,
    }
    response = client.complete(
        [
            LlmMessage(role="system", content=COMPOSE_SYSTEM.replace("MAX_WORDS", str(max_words))),
            LlmMessage(
                role="user",
                content=(
                    f"RESUME FACTS (only use these):\n{resume_blob}\n\n"
                    f"POST + SEND CONTEXT:\n{json.dumps(payload, ensure_ascii=False)}"
                ),
            ),
        ],
        temperature=temperature,
        response_format_json=True,
    )
    try:
        draft = _parse_draft(response.content)
    except Exception as exc:
        retry = client.complete(
            [
                LlmMessage(role="system", content=COMPOSE_SYSTEM.replace("MAX_WORDS", str(max_words))),
                LlmMessage(
                    role="user",
                    content=(
                        f"Previous JSON failed ({exc}). Return corrected JSON only.\n"
                        f"RESUME:\n{resume_blob}\n\nCONTEXT:\n{json.dumps(payload)}"
                    ),
                ),
            ],
            temperature=temperature,
            response_format_json=True,
        )
        draft = _parse_draft(retry.content)

    return validate_draft(draft, max_words=max_words, post_text=post.get("raw_text", ""))


def _emails_with_open_drafts() -> set[str]:
    open_states = (DraftStatus.pending_review, DraftStatus.approved, DraftStatus.edited)
    with session_scope() as session:
        rows = session.exec(
            select(EmailDraft.to_email).where(EmailDraft.status.in_(open_states))  # type: ignore[attr-defined]
        ).all()
    return {e.strip().lower() for e in rows if e}


def _mark_duplicate(post_id: int, email: str) -> None:
    with session_scope() as session:
        db = session.get(Post, post_id)
        if db is None:
            return
        db.status = PostStatus.skipped
        db.last_error = f"already emailed or drafted: {email}"
        db.updated_at = utcnow()
        session.add(db)
    log.info("compose_skip_duplicate", post_id=post_id, to=email)


def run(*, dry_run: bool = False) -> StageResult:
    settings = get_settings()
    cfg = settings.config.compose

    with session_scope() as session:
        rows = list(
            session.exec(
                select(Post)
                .where(Post.status == PostStatus.matched)
                .where(Post.contact_email.is_not(None))  # type: ignore[union-attr]
                .order_by(Post.id)
            ).all()
        )
        posts = [
            {
                "id": p.id,
                "url": p.url,
                "role_title": p.role_title,
                "company_name": p.company_name,
                "location": p.location,
                "contact_name": p.contact_name,
                "contact_email": p.contact_email,
                "raw_text": p.raw_text,
            }
            for p in rows
        ]

    details = [f"candidates={len(posts)}", f"max_words={cfg.max_words}"]
    if not posts:
        return StageResult(stage="compose", dry_run=dry_run, details=details + ["nothing to compose"])

    if dry_run:
        for p in posts[:10]:
            details.append(
                f"id={p['id']} to={p['contact_email']} role={p['role_title']!r} would_draft=yes"
            )
        if len(posts) > 10:
            details.append(f"... and {len(posts) - 10} more")
        details.append("dry-run: no LLM calls, no DB writes")
        return StageResult(stage="compose", dry_run=True, processed=len(posts), details=details)

    profile = load_or_build_profile(settings)
    resume_blob = profile.facts_blob()
    if profile.raw_text and (not profile.skills or not profile.experience):
        resume_blob = f"{resume_blob}\n\nFULL RESUME TEXT:\n{profile.raw_text[:8000]}"

    your_name = cfg.your_name
    if your_name == "Your Name" and profile.full_name:
        your_name = profile.full_name
    your_headline = cfg.your_headline or (profile.headline or "")

    client = get_llm_client(settings)
    succeeded = refused = failed = duplicates = 0
    claimed = _emails_with_open_drafts()

    for post in posts:
        email = post["contact_email"].strip().lower()
        if email in claimed or cooldown_blocks(email):
            _mark_duplicate(post["id"], email)
            duplicates += 1
            continue
        claimed.add(email)
        try:
            draft = generate_draft(
                resume_blob=resume_blob,
                post=post,
                your_name=your_name,
                your_headline=your_headline,
                max_words=cfg.max_words,
                client=client,
                temperature=settings.config.llm.temperature,
            )
            with session_scope() as session:
                db = session.get(Post, post["id"])
                if db is None:
                    continue
                if draft.refusal:
                    db.status = PostStatus.compose_refused
                    db.last_error = draft.refusal_reason
                    refused += 1
                else:
                    session.add(
                        EmailDraft(
                            post_id=db.id,  # type: ignore[arg-type]
                            to_email=post["contact_email"],
                            subject=draft.subject,
                            body=draft.body,
                            status=DraftStatus.pending_review,
                            refusal=False,
                            created_at=utcnow(),
                            updated_at=utcnow(),
                        )
                    )
                    db.status = PostStatus.pending_review
                    succeeded += 1
                db.updated_at = utcnow()
                session.add(db)
            log.info(
                "compose_done",
                post_id=post["id"],
                refusal=draft.refusal,
                subject=(draft.subject or "")[:80],
            )
        except LlmUnavailable as exc:
            details.append(f"stopped early, posts stay matched for next run: {exc}")
            log.error("compose_llm_unavailable", error=str(exc))
            break
        except Exception as exc:
            failed += 1
            log.exception("compose_failed", post_id=post["id"], error=str(exc))
            with session_scope() as session:
                db = session.get(Post, post["id"])
                if db:
                    db.last_error = str(exc)[:2000]
                    db.updated_at = utcnow()
                    session.add(db)

    details.append(
        f"drafts={succeeded} refused={refused} failed={failed} skipped_already_emailed={duplicates}"
    )
    return StageResult(
        stage="compose",
        dry_run=False,
        processed=len(posts),
        succeeded=succeeded,
        skipped=refused,
        failed=failed,
        details=details,
    )
