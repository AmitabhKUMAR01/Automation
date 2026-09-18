"""Send stage: throttled Gmail delivery of approved drafts."""

from __future__ import annotations

import time

from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from outreach.config import get_settings
from outreach.db.models import DraftStatus, EmailDraft, Post, PostStatus, RecipientCooldown, SendLog, utcnow
from outreach.db.session import session_scope
from outreach.logging import get_logger
from outreach.send.gmail import send_email
from outreach.send.guards import (
    cooldown_blocks,
    normalize_email,
    purge_expired_cooldowns,
    random_send_delay_seconds,
    sends_today,
    within_business_hours,
)
from outreach.stages import StageResult

log = get_logger("send")


def run(*, dry_run: bool = False) -> StageResult:
    settings = get_settings()
    cfg = settings.config.send

    if cfg.require_approval:
        status_filter = DraftStatus.approved
    else:
        # Explicit disable of approval: allow pending_review too
        status_filter = None

    with session_scope() as session:
        q = select(EmailDraft).order_by(EmailDraft.id)
        if status_filter is not None:
            q = q.where(EmailDraft.status == status_filter)
        else:
            q = q.where(
                EmailDraft.status.in_([DraftStatus.approved, DraftStatus.pending_review])  # type: ignore[arg-type]
            )
        drafts = list(session.exec(q).all())
        items = [
            {
                "id": d.id,
                "post_id": d.post_id,
                "to_email": d.to_email,
                "subject": d.subject,
                "body": d.body,
                "status": d.status,
            }
            for d in drafts
        ]

    details = [
        f"candidates={len(items)}",
        f"require_approval={cfg.require_approval}",
        f"max_sends_per_day={cfg.max_sends_per_day}",
    ]

    if not items:
        return StageResult(
            stage="send",
            dry_run=dry_run,
            details=details + ["no approved drafts (run `pipeline review` first)"],
        )

    ok_hours, reason = within_business_hours(settings)
    already = sends_today(settings)
    details.append(f"sends_today={already}")
    details.append(f"business_hours={'yes' if ok_hours else 'no:' + reason}")

    if dry_run:
        for it in items[:10]:
            blocked = cooldown_blocks(it["to_email"])
            details.append(
                f"draft={it['id']} to={it['to_email']} cooldown_block={blocked} would_send=yes"
            )
        details.append("dry-run: no Gmail calls, no DB writes")
        return StageResult(stage="send", dry_run=True, processed=len(items), details=details)

    if not ok_hours:
        return StageResult(
            stage="send",
            dry_run=False,
            skipped=len(items),
            details=details + [f"blocked: {reason}"],
        )

    purged = purge_expired_cooldowns(settings)
    if purged:
        details.append(f"purged_expired_cooldowns={purged}")

    sent = skipped = failed = 0
    remaining_quota = max(0, cfg.max_sends_per_day - already)

    for idx, it in enumerate(items):
        if sent >= remaining_quota:
            skipped += len(items) - idx
            details.append("daily send limit reached")
            break

        email = normalize_email(it["to_email"])
        if cooldown_blocks(email):
            skipped += 1
            details.append(f"draft={it['id']} skipped cooldown {email}")
            continue

        # Delay between sends (not before the first)
        if sent > 0:
            delay = random_send_delay_seconds(settings)
            log.info("send_throttle_sleep", seconds=round(delay, 1))
            time.sleep(delay)
            ok_hours, reason = within_business_hours(settings)
            if not ok_hours:
                skipped += len(items) - idx
                details.append(f"stopped: {reason}")
                break

        try:
            # Reserve cooldown row first (DB-level uniqueness)
            try:
                with session_scope() as session:
                    session.add(
                        RecipientCooldown(
                            email_normalized=email,
                            draft_id=it["id"],
                            sent_at=utcnow(),
                        )
                    )
                    session.flush()
            except IntegrityError:
                skipped += 1
                details.append(f"draft={it['id']} skipped unique cooldown {email}")
                continue

            message_id = send_email(
                settings,
                to_email=email,
                subject=it["subject"],
                body=it["body"],
            )

            with session_scope() as session:
                draft = session.get(EmailDraft, it["id"])
                post = session.get(Post, it["post_id"])
                session.add(
                    SendLog(
                        draft_id=it["id"],
                        post_id=it["post_id"],
                        to_email=email,
                        subject=it["subject"],
                        body=it["body"],
                        gmail_message_id=message_id,
                        sent_at=utcnow(),
                    )
                )
                if draft:
                    draft.status = DraftStatus.sent
                    draft.updated_at = utcnow()
                    session.add(draft)
                if post:
                    post.status = PostStatus.sent
                    post.updated_at = utcnow()
                    session.add(post)
            sent += 1
            log.info("send_ok", draft_id=it["id"], to=email, message_id=message_id)
        except Exception as exc:
            failed += 1
            log.exception("send_failed", draft_id=it["id"], error=str(exc))
            # Roll back cooldown reservation on failure so retries are possible
            with session_scope() as session:
                row = session.exec(
                    select(RecipientCooldown).where(RecipientCooldown.email_normalized == email)
                ).first()
                if row and row.draft_id == it["id"]:
                    session.delete(row)
                draft = session.get(EmailDraft, it["id"])
                if draft:
                    draft.updated_at = utcnow()
                    session.add(draft)

    details.append(f"sent={sent} skipped={skipped} failed={failed}")
    return StageResult(
        stage="send",
        dry_run=False,
        processed=len(items),
        succeeded=sent,
        skipped=skipped,
        failed=failed,
        details=details,
    )
