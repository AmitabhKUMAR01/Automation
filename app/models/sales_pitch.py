import uuid
from sqlalchemy import Column, String, DateTime, Integer, Boolean, JSON, ForeignKey, Text, func
from app.config.database import Base


class SalesPitch(Base):
    __tablename__ = "sales_pitches"

    id = Column(Integer, primary_key=True)
    uuid = Column(
        String(36),
        nullable=False,
        unique=True,
        index=True,
        default=lambda: str(uuid.uuid4()),
    )
    business_client_id  = Column(Integer, ForeignKey("business_clients.id"),  nullable=True, index=True)
    lead_score_id       = Column(Integer, ForeignKey("lead_scores.id"),       nullable=True, index=True)
    # NULL for email / generic pitches; set when pitch is for a specific LinkedIn contact
    linkedin_contact_id = Column(Integer, ForeignKey("linkedin_contacts.id"), nullable=True,  index=True)
    # NULL for client pitches; set when pitch is for a specific LinkedIn search-based contact
    linkedin_search_contact_id = Column(Integer, ForeignKey("linkedin_search_contacts.id"), nullable=True, index=True)

    # ---------- Pitch Content ----------
    pitch_subject  = Column(Text, nullable=True)
    pitch_hook     = Column(Text, nullable=True)
    pitch_body     = Column(Text, nullable=True)

    # ---------- Supporting Data ----------
    key_pain_points = Column(JSON, nullable=True)

    # "gemini" | "openai" | "rule_engine"
    generated_by   = Column(String(50), nullable=True)
    # "linkedin" | "email" | "generic"
    pitch_channel  = Column(String(20), nullable=True, default="generic")

    # ---------- Timestamps ----------
    generated_at = Column(DateTime(timezone=True), nullable=True)
    created_at   = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # ---------- Delivery Tracking ----------
    # "pending" | "sent" | "failed" | "skipped"
    delivery_status   = Column(String(20), nullable=True, default="pending")
    delivered_at      = Column(DateTime(timezone=True), nullable=True)
    delivery_error    = Column(Text, nullable=True)   # last error message
    delivery_attempts = Column(Integer, default=0, nullable=False)

    # ---------- Reply Tracking ----------
    reply_received   = Column(Boolean, default=False, nullable=False)   # True when contact replied
    reply_text       = Column(Text, nullable=True)                       # first reply message (capped at 2000 chars)
    replied_at       = Column(DateTime(timezone=True), nullable=True)   # when we detected the reply
    reply_checked_at = Column(DateTime(timezone=True), nullable=True)   # last time we checked for a reply
