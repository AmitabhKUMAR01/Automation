"""What the DB already has — lets scrapers aim for *new* sendable posts."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlmodel import select

from outreach.db.models import Post, RecipientCooldown, SendLog
from outreach.db.session import session_scope
from outreach.parse.emails import extract_emails, normalize_email


@dataclass
class KnownInventory:
    urls: set[str] = field(default_factory=set)
    emails: set[str] = field(default_factory=set)

    def new_emails(self, text: str) -> list[str]:
        return [e for e in extract_emails(text) if normalize_email(e) not in self.emails]


def load_known_inventory() -> KnownInventory:
    inv = KnownInventory()
    with session_scope() as session:
        for url, email in session.exec(select(Post.url, Post.contact_email)).all():
            inv.urls.add(url)
            if email:
                inv.emails.add(normalize_email(email))
        for column in (SendLog.to_email, RecipientCooldown.email_normalized):
            for email in session.exec(select(column)).all():
                if email:
                    inv.emails.add(normalize_email(email))
    return inv
