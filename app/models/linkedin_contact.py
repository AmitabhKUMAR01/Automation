import uuid
from sqlalchemy import Column, String, DateTime, Integer, Boolean, ForeignKey, func
from app.config.database import Base


class LinkedinContact(Base):
    __tablename__ = "linkedin_contacts"

    id = Column(Integer, primary_key=True)
    uuid = Column(
        String(36),
        nullable=False,
        unique=True,
        index=True,
        default=lambda: str(uuid.uuid4()),
    )

    # Relationship to the company this person works at
    business_client_id = Column(
        Integer,
        ForeignKey("business_clients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Person details scraped from LinkedIn
    name        = Column(String(255), nullable=True)
    job_title   = Column(String(255), nullable=True)   # as shown on LinkedIn headline
    profile_url = Column(String(500), nullable=False)  # linkedin.com/in/<slug>

    match_confidence = Column(String(10), nullable=True, default="medium")

    # Connection tracking
    connection_sent    = Column(Boolean, default=False, nullable=False)
    connection_sent_at = Column(DateTime(timezone=True), nullable=True)

    # Acceptance tracking
    is_connected  = Column(Boolean, default=False, nullable=False)
    connected_at  = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        onupdate=func.now(),
        nullable=True,
    )
    deleted_at = Column(DateTime(timezone=True), nullable=True)
        
