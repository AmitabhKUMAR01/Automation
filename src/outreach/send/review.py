"""Interactive approval queue for pending drafts."""

from __future__ import annotations

from sqlmodel import select

from outreach.config import get_settings
from outreach.db.models import CompanyBlacklist, DraftStatus, EmailDraft, Post, PostStatus, utcnow
from outreach.db.session import session_scope
from outreach.logging import get_logger
from outreach.stages import StageResult
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.columns import Columns

log = get_logger("send.review")
console = Console()


def _normalize_company(name: str) -> str:
    return " ".join(name.strip().lower().split())


def review(*, dry_run: bool = False) -> StageResult:
    settings = get_settings()
    with session_scope() as session:
        drafts = list(
            session.exec(
                select(EmailDraft)
                .where(EmailDraft.status == DraftStatus.pending_review)
                .order_by(EmailDraft.id)
            ).all()
        )
        items = []
        for d in drafts:
            post = session.get(Post, d.post_id)
            items.append(
                {
                    "draft_id": d.id,
                    "post_id": d.post_id,
                    "to_email": d.to_email,
                    "subject": d.subject,
                    "body": d.body,
                    "role": post.role_title if post else None,
                    "company": post.company_name if post else None,
                    "post_text": (post.raw_text[:1200] if post else ""),
                    "url": post.url if post else "",
                    "score": post.match_score if post else None,
                }
            )

    details = [f"pending={len(items)}"]
    if not items:
        return StageResult(stage="review", dry_run=dry_run, details=details + ["no drafts pending"])

    if dry_run:
        for it in items[:10]:
            details.append(
                f"draft={it['draft_id']} to={it['to_email']} subject={it['subject']!r}"
            )
        details.append("dry-run: no approve/reject writes")
        return StageResult(stage="review", dry_run=True, processed=len(items), details=details)

    approved = rejected = edited = blacklisted = 0

    for it in items:
        left = Panel(
            f"[bold]{it['subject']}[/bold]\nTo: {it['to_email']}\n\n{it['body']}",
            title=f"Draft #{it['draft_id']}",
            border_style="green",
        )
        right = Panel(
            f"Role: {it['role']}\nCompany: {it['company']}\nScore: {it['score']}\n"
            f"URL: {it['url']}\n\n{it['post_text']}",
            title=f"Post #{it['post_id']}",
            border_style="cyan",
        )
        console.print(Columns([left, right], equal=True, expand=True))
        choice = Prompt.ask(
            "Action",
            choices=["a", "e", "r", "b", "s", "q"],
            default="s",
            show_choices=True,
        )
        # a=approve e=edit r=reject b=reject+blacklist s=skip q=quit

        if choice == "q":
            details.append("quit")
            break
        if choice == "s":
            details.append(f"draft={it['draft_id']} skipped")
            continue

        if choice == "e":
            new_subject = Prompt.ask("Subject", default=it["subject"])
            console.print("Enter body. Finish with a single line containing only END")
            lines: list[str] = []
            while True:
                line = console.input()
                if line.strip() == "END":
                    break
                lines.append(line)
            new_body = "\n".join(lines).strip() or it["body"]
            with session_scope() as session:
                draft = session.get(EmailDraft, it["draft_id"])
                post = session.get(Post, it["post_id"])
                if draft:
                    draft.subject = new_subject
                    draft.body = new_body
                    draft.status = DraftStatus.approved
                    draft.reviewed_at = utcnow()
                    draft.updated_at = utcnow()
                    session.add(draft)
                if post:
                    post.status = PostStatus.approved
                    post.updated_at = utcnow()
                    session.add(post)
            edited += 1
            approved += 1
            continue

        if choice == "a":
            with session_scope() as session:
                draft = session.get(EmailDraft, it["draft_id"])
                post = session.get(Post, it["post_id"])
                if draft:
                    draft.status = DraftStatus.approved
                    draft.reviewed_at = utcnow()
                    draft.updated_at = utcnow()
                    session.add(draft)
                if post:
                    post.status = PostStatus.approved
                    post.updated_at = utcnow()
                    session.add(post)
            approved += 1
            continue

        if choice in {"r", "b"}:
            with session_scope() as session:
                draft = session.get(EmailDraft, it["draft_id"])
                post = session.get(Post, it["post_id"])
                if draft:
                    draft.status = DraftStatus.rejected
                    draft.reviewed_at = utcnow()
                    draft.updated_at = utcnow()
                    session.add(draft)
                if post:
                    post.status = PostStatus.rejected
                    post.updated_at = utcnow()
                    session.add(post)
                if choice == "b" and it["company"]:
                    norm = _normalize_company(it["company"])
                    existing = session.exec(
                        select(CompanyBlacklist).where(
                            CompanyBlacklist.company_name_normalized == norm
                        )
                    ).first()
                    if existing is None:
                        session.add(
                            CompanyBlacklist(
                                company_name=it["company"],
                                company_name_normalized=norm,
                                reason="reject-and-blacklist from review",
                            )
                        )
                    blacklisted += 1
            rejected += 1
            continue

    details.append(
        f"approved={approved} rejected={rejected} edited={edited} blacklisted={blacklisted}"
    )
    _ = settings
    return StageResult(
        stage="review",
        dry_run=False,
        processed=len(items),
        succeeded=approved,
        skipped=rejected,
        details=details,
    )
