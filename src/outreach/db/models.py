"""SQLModel entities shared across all pipeline stages."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from sqlalchemy import JSON, Column, Index, Text, UniqueConstraint
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PostStatus(str, Enum):
    """Lifecycle of a hiring post through the pipeline."""

    ingested = "ingested"
    parsed = "parsed"
    needs_enrichment = "needs_enrichment"
    enriched = "enriched"
    matched = "matched"
    rejected_match = "rejected_match"
    composed = "composed"
    compose_refused = "compose_refused"
    pending_review = "pending_review"
    approved = "approved"
    rejected = "rejected"
    sent = "sent"
    skipped = "skipped"
    failed = "failed"


class DraftStatus(str, Enum):
    pending_review = "pending_review"
    approved = "approved"
    rejected = "rejected"
    sent = "sent"
    edited = "edited"


class Post(SQLModel, table=True):
    """One hiring post. Stages mutate fields; status drives resume-after-crash."""

    __tablename__ = "posts"
    __table_args__ = (UniqueConstraint("url", name="uq_posts_url"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    url: str = Field(index=True, max_length=1024)
    source: str = Field(default="apify", max_length=64)
    author_name: Optional[str] = Field(default=None, max_length=512)
    author_profile_url: Optional[str] = Field(default=None, max_length=1024)
    raw_text: str = Field(sa_column=Column(Text, nullable=False))
    posted_at: Optional[datetime] = Field(default=None, index=True)
    ingested_at: datetime = Field(default_factory=utcnow)

    # Parse / enrich outputs
    role_title: Optional[str] = Field(default=None, max_length=512)
    company_name: Optional[str] = Field(default=None, max_length=512, index=True)
    location: Optional[str] = Field(default=None, max_length=512)
    experience_required: Optional[str] = Field(default=None, max_length=256)
    skills: Optional[list[Any]] = Field(default=None, sa_column=Column(JSON))
    contact_name: Optional[str] = Field(default=None, max_length=512)
    contact_email: Optional[str] = Field(default=None, max_length=320, index=True)
    application_method: Optional[str] = Field(default=None, max_length=128)
    company_domain: Optional[str] = Field(default=None, max_length=256)
    email_confidence: Optional[float] = Field(default=None)

    # Match outputs
    match_score: Optional[int] = Field(default=None, index=True)
    match_reasoning: Optional[str] = Field(default=None, sa_column=Column(Text))
    matched_skills: Optional[list[Any]] = Field(default=None, sa_column=Column(JSON))
    skill_gaps: Optional[list[Any]] = Field(default=None, sa_column=Column(JSON))

    status: PostStatus = Field(default=PostStatus.ingested, index=True)
    last_error: Optional[str] = Field(default=None, sa_column=Column(Text))
    updated_at: datetime = Field(default_factory=utcnow)


class EmailDraft(SQLModel, table=True):
    """Composed email awaiting human review / send."""

    __tablename__ = "email_drafts"

    id: Optional[int] = Field(default=None, primary_key=True)
    post_id: int = Field(foreign_key="posts.id", index=True)
    to_email: str = Field(max_length=320, index=True)
    subject: str = Field(max_length=998)
    body: str = Field(sa_column=Column(Text, nullable=False))
    status: DraftStatus = Field(default=DraftStatus.pending_review, index=True)
    refusal: bool = Field(default=False)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    reviewed_at: Optional[datetime] = Field(default=None)


class SendLog(SQLModel, table=True):
    """Immutable record of every outbound message."""

    __tablename__ = "send_log"

    id: Optional[int] = Field(default=None, primary_key=True)
    draft_id: int = Field(foreign_key="email_drafts.id", index=True)
    post_id: int = Field(foreign_key="posts.id", index=True)
    to_email: str = Field(max_length=320, index=True)
    subject: str = Field(max_length=998)
    body: str = Field(sa_column=Column(Text, nullable=False))
    gmail_message_id: Optional[str] = Field(default=None, max_length=256)
    sent_at: datetime = Field(default_factory=utcnow, index=True)


class RecipientCooldown(SQLModel, table=True):
    """
    DB-level uniqueness: one row per normalized email.

    Insert fails (UNIQUE) if we already emailed this address. Rows older than
    `email_cooldown_days` are deleted before send attempts so the address
    becomes available again after the window.
    """

    __tablename__ = "recipient_cooldowns"
    __table_args__ = (
        UniqueConstraint("email_normalized", name="uq_recipient_email"),
        Index("ix_recipient_cooldowns_sent_at", "sent_at"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    email_normalized: str = Field(max_length=320)
    draft_id: int = Field(foreign_key="email_drafts.id")
    sent_at: datetime = Field(default_factory=utcnow)


class CompanyBlacklist(SQLModel, table=True):
    """Companies rejected via review CLI (`reject-and-blacklist-company`)."""

    __tablename__ = "company_blacklist"
    __table_args__ = (UniqueConstraint("company_name_normalized", name="uq_blacklist_company"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    company_name: str = Field(max_length=512)
    company_name_normalized: str = Field(max_length=512)
    reason: Optional[str] = Field(default=None, max_length=1024)
    created_at: datetime = Field(default_factory=utcnow)


class ContactStatus(str, Enum):
    discovered = "discovered"  # scraped from connections, profile not verified yet
    excluded = "excluded"  # not a target (company rule, seniority, ...)
    pending_review = "pending_review"
    approved = "approved"
    sent = "sent"
    skipped = "skipped"  # rejected in review, or existing conversation at send time
    failed = "failed"


class LinkedInContact(SQLModel, table=True):
    """A 1st-degree LinkedIn connection considered for a job-ask message."""

    __tablename__ = "linkedin_contacts"
    __table_args__ = (UniqueConstraint("profile_url", name="uq_linkedin_contact_url"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    profile_url: str = Field(max_length=1024, index=True)
    name: str = Field(max_length=512)
    headline: Optional[str] = Field(default=None, max_length=1024)
    company: Optional[str] = Field(default=None, max_length=512)
    connected_text: Optional[str] = Field(default=None, max_length=256)
    connected_days: Optional[int] = Field(default=None)
    category: Optional[str] = Field(default=None, max_length=32)  # hr | leader | senior | experienced
    years_experience: Optional[int] = Field(default=None)
    profile_checked: bool = Field(default=False)
    status: ContactStatus = Field(default=ContactStatus.discovered, index=True)
    excluded_reason: Optional[str] = Field(default=None, max_length=512)
    message: Optional[str] = Field(default=None, sa_column=Column(Text))
    last_error: Optional[str] = Field(default=None, sa_column=Column(Text))
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    reviewed_at: Optional[datetime] = Field(default=None)
    sent_at: Optional[datetime] = Field(default=None)


class LinkedInMessageLog(SQLModel, table=True):
    """One row per LinkedIn message sent. UNIQUE profile_url = never message twice."""

    __tablename__ = "linkedin_message_log"
    __table_args__ = (UniqueConstraint("profile_url", name="uq_linkedin_message_profile"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    contact_id: int = Field(foreign_key="linkedin_contacts.id", index=True)
    profile_url: str = Field(max_length=1024)
    message: str = Field(sa_column=Column(Text, nullable=False))
    sent_at: datetime = Field(default_factory=utcnow, index=True)


class ProspectStatus(str, Enum):
    found = "found"
    excluded = "excluded"
    pending_review = "pending_review"
    approved = "approved"
    invited = "invited"
    accepted = "accepted"  # now a 1st-degree connection (seen by connections-sync)
    skipped = "skipped"  # rejected in review, already pending/connected, follow-only, ...
    failed = "failed"


class LinkedInProspect(SQLModel, table=True):
    """A not-yet-connected person found via people search, considered for an invite."""

    __tablename__ = "linkedin_prospects"
    __table_args__ = (UniqueConstraint("profile_url", name="uq_linkedin_prospect_url"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    profile_url: str = Field(max_length=1024, index=True)
    name: str = Field(max_length=512)
    headline: Optional[str] = Field(default=None, max_length=1024)
    location: Optional[str] = Field(default=None, max_length=256)
    category: Optional[str] = Field(default=None, max_length=32)
    search_query: Optional[str] = Field(default=None, max_length=256)
    status: ProspectStatus = Field(default=ProspectStatus.pending_review, index=True)
    excluded_reason: Optional[str] = Field(default=None, max_length=512)
    last_error: Optional[str] = Field(default=None, sa_column=Column(Text))
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    reviewed_at: Optional[datetime] = Field(default=None)
    invited_at: Optional[datetime] = Field(default=None, index=True)
    accepted_at: Optional[datetime] = Field(default=None)


class ResumeCache(SQLModel, table=True):
    """Cached structured profile parsed from the resume PDF."""

    __tablename__ = "resume_cache"

    id: Optional[int] = Field(default=None, primary_key=True)
    source_path: str = Field(max_length=1024)
    source_mtime: float = Field(description="mtime of the PDF when parsed")
    raw_text: str = Field(sa_column=Column(Text, nullable=False))
    profile_json: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    parsed_at: datetime = Field(default_factory=utcnow)
