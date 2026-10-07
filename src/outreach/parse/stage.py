"""Parse stage: regex emails first, then LLM structured extraction."""

from __future__ import annotations

import json
import re

from sqlmodel import select

from outreach.config import get_settings
from outreach.db.models import Post, PostStatus, utcnow
from outreach.db.session import session_scope
from outreach.llm import LlmUnavailable, get_llm_client
from outreach.logging import get_logger
from outreach.parse.emails import extract_emails
from outreach.send.guards import cooldown_blocks
from outreach.parse.schema import ParsedPostFields
from outreach.protocols import LlmClient, LlmMessage
from outreach.stages import StageResult

log = get_logger("parse")

_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)

SYSTEM_PROMPT = """You extract structured hiring-post fields from LinkedIn post text.
Return a single JSON object with exactly these keys:
role_title, company_name, location, experience_required, skills, contact_name,
contact_email, application_method.

Rules:
- skills must be an array of strings (empty if unknown).
- application_method is one of: email, linkedin, form, other, or null.
- Use null for unknown scalars. Do not invent emails or companies not supported by the text.
- If multiple roles appear, pick the primary one the poster is hiring for.
- contact_email: only if clearly present in the text (including de-obfuscated forms).
"""


def _strip_json(content: str) -> str:
    content = content.strip()
    fence = _JSON_FENCE.search(content)
    if fence:
        return fence.group(1).strip()
    return content


def parse_llm_json(content: str) -> ParsedPostFields:
    data = json.loads(_strip_json(content))
    return ParsedPostFields.model_validate(data)


def extract_with_llm(
    text: str,
    *,
    client: LlmClient,
    temperature: float,
    retries: int,
) -> ParsedPostFields:
    messages = [
        LlmMessage(role="system", content=SYSTEM_PROMPT),
        LlmMessage(role="user", content=f"Post text:\n\n{text}"),
    ]
    attempts = max(1, retries + 1)
    last_error: Exception | None = None
    for attempt in range(attempts):
        response = client.complete(
            messages,
            temperature=temperature,
            response_format_json=True,
        )
        try:
            return parse_llm_json(response.content)
        except Exception as exc:  # noqa: BLE001 — retry once on schema/parse failure
            last_error = exc
            log.warning("parse_llm_invalid", attempt=attempt + 1, error=str(exc))
            messages = [
                *messages,
                LlmMessage(role="assistant", content=response.content),
                LlmMessage(
                    role="user",
                    content=(
                        f"Your previous response failed validation: {exc}. "
                        "Return corrected JSON only."
                    ),
                ),
            ]
    raise RuntimeError(f"LLM parse failed after {attempts} attempts: {last_error}")


def apply_parsed_fields(post: Post, fields: ParsedPostFields, regex_emails: list[str]) -> None:
    post.role_title = fields.role_title
    post.company_name = fields.company_name
    post.location = fields.location
    post.experience_required = fields.experience_required
    post.skills = fields.skills
    post.contact_name = fields.contact_name
    post.application_method = fields.application_method

    email = fields.contact_email or (regex_emails[0] if regex_emails else None)
    # Prefer regex if LLM invents something not in text / regex found one
    if regex_emails:
        email = regex_emails[0]
    post.contact_email = email

    if post.contact_email:
        post.status = PostStatus.parsed
        if not post.application_method:
            post.application_method = "email"
    else:
        post.status = PostStatus.needs_enrichment

    post.last_error = None
    post.updated_at = utcnow()


def _mark_without_llm(post_id: int, status: PostStatus, email: str | None, note: str | None) -> None:
    with session_scope() as session:
        db_post = session.get(Post, post_id)
        if db_post is None:
            return
        db_post.status = status
        db_post.contact_email = email
        db_post.last_error = note
        db_post.updated_at = utcnow()
        session.add(db_post)


def run(*, dry_run: bool = False) -> StageResult:
    settings = get_settings()
    with session_scope() as session:
        rows = list(
            session.exec(
                select(Post).where(Post.status == PostStatus.ingested).order_by(Post.id)
            ).all()
        )
        # Snapshot fields so objects are safe outside the session
        posts = [
            {
                "id": p.id,
                "url": p.url,
                "raw_text": p.raw_text,
            }
            for p in rows
        ]

    details: list[str] = [f"candidates={len(posts)}"]
    if not posts:
        return StageResult(stage="parse", dry_run=dry_run, details=details + ["nothing to parse"])

    if dry_run:
        for post in posts[:10]:
            emails = extract_emails(post["raw_text"])
            details.append(
                f"id={post['id']} emails={emails or ['(none)']} would_llm=yes url={post['url'][:80]}"
            )
        if len(posts) > 10:
            details.append(f"... and {len(posts) - 10} more")
        details.append("dry-run: no LLM calls, no DB writes")
        return StageResult(
            stage="parse",
            dry_run=True,
            processed=len(posts),
            details=details,
        )

    client = get_llm_client(settings)
    succeeded = 0
    failed = 0
    needs_enrichment = 0
    already_emailed = 0
    llm_down: str | None = None

    for post in posts:
        post_id = post["id"]
        regex_emails = extract_emails(post["raw_text"])
        # Enrichment is the only path for email-less posts, so the LLM adds nothing there;
        # free-tier quotas are too small to spend on them.
        if not regex_emails:
            _mark_without_llm(post_id, PostStatus.needs_enrichment, None, None)
            needs_enrichment += 1
            continue
        fresh = [e for e in regex_emails if not cooldown_blocks(e)]
        if not fresh:
            _mark_without_llm(
                post_id, PostStatus.skipped, regex_emails[0], f"already emailed: {regex_emails[0]}"
            )
            already_emailed += 1
            continue
        if llm_down:
            continue
        try:
            fields = extract_with_llm(
                post["raw_text"],
                client=client,
                temperature=settings.config.llm.temperature,
                retries=settings.config.parse.llm_retries,
            )
            with session_scope() as session:
                db_post = session.get(Post, post_id)
                if db_post is None:
                    continue
                apply_parsed_fields(db_post, fields, fresh)
                if db_post.status == PostStatus.needs_enrichment:
                    needs_enrichment += 1
                session.add(db_post)
            succeeded += 1
            log.info(
                "parse_ok",
                post_id=post_id,
                email=bool(regex_emails or fields.contact_email),
                role=fields.role_title,
            )
        except LlmUnavailable as exc:
            llm_down = str(exc)
            log.error("parse_llm_unavailable", error=llm_down)
        except Exception as exc:
            failed += 1
            log.exception("parse_failed", post_id=post_id, error=str(exc))
            with session_scope() as session:
                db_post = session.get(Post, post_id)
                if db_post is not None:
                    db_post.last_error = str(exc)[:2000]
                    db_post.updated_at = utcnow()
                    # leave status=ingested so a re-run retries this post
                    session.add(db_post)

    details.append(
        f"ok={succeeded} failed={failed} needs_enrichment={needs_enrichment} "
        f"already_emailed={already_emailed}"
    )
    if llm_down:
        details.append(f"stopped early, posts left as ingested for next run: {llm_down}")
    return StageResult(
        stage="parse",
        dry_run=False,
        processed=len(posts),
        succeeded=succeeded,
        failed=failed,
        skipped=needs_enrichment,
        details=details,
    )
