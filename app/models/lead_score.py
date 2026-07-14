import uuid
from sqlalchemy import Column, String, DateTime, Integer, Float, Boolean, JSON, ForeignKey, func
from app.config.database import Base


class LeadScore(Base):
    __tablename__ = "lead_scores"

    id = Column(Integer, primary_key=True)
    uuid = Column(
        String(36),
        nullable=False,
        unique=True,
        index=True,
        default=lambda: str(uuid.uuid4()),
    )
    business_client_id = Column(Integer, ForeignKey("business_clients.id"), nullable=False, index=True)
    website_audit_id   = Column(Integer, ForeignKey("website_audits.id"),   nullable=False, index=True)

    # ---------- Score ----------
    total_score     = Column(Float, nullable=False)         
    grade           = Column(String(2), nullable=False)

    # ---------- Breakdown (JSON) ----------
    score_breakdown = Column(JSON, nullable=True)

    # ---------- Issues (JSON) ----------
    issues_found    = Column(JSON, nullable=True)

    # ---------- Timestamps ----------
    scored_at  = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
