import uuid
from sqlalchemy import Column, String, DateTime, Integer, Float, func
from app.config.database import Base


class ScrapeSession(Base):
    __tablename__ = "scrape_sessions"

    id = Column(Integer, primary_key=True)
    uuid = Column(
        String(36),
        nullable=False,
        unique=True,
        index=True,
        default=lambda: str(uuid.uuid4()),
    )
    # What was scraped
    query        = Column(String(500), nullable=False)
    category     = Column(String(255), nullable=True)
    city         = Column(String(255), nullable=True)
    state        = Column(String(255), nullable=True)
    country      = Column(String(255), nullable=True)

    # Result counts
    total_scraped        = Column(Integer, default=0, nullable=False)
    total_saved          = Column(Integer, default=0, nullable=False)
    skipped_duplicates   = Column(Integer, default=0, nullable=False)

    # Timing
    started_at   = Column(DateTime(timezone=True), nullable=False)
    finished_at  = Column(DateTime(timezone=True), nullable=True)
    duration_sec = Column(Float, nullable=True)   # seconds taken to complete

    # Status: 'running' | 'completed' | 'failed'
    status       = Column(String(50), default="running", nullable=False)
    error        = Column(String(1000), nullable=True)

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
