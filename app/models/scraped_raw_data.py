import uuid
from sqlalchemy import Column, String, DateTime, Integer, JSON, func, ForeignKey
from app.config.database import Base


class ScrapedRawData(Base):
    __tablename__ = "scraped_raw_data"

    id = Column(Integer, primary_key=True, index=True)
    uuid = Column(
        String(36),
        nullable=False,
        unique=True,
        index=True,
        default=lambda: str(uuid.uuid4()),
    )
    business_client_id = Column(Integer, ForeignKey("business_clients.id"), nullable=False, index=True)
    data          = Column(JSON,        nullable=True)
    scrape_source = Column(String(255), nullable=True)   # e.g. "google_maps"
    search_engine = Column(String(255), nullable=True)   # e.g. "Google"
    browser       = Column(String(255), nullable=True)   # e.g. "Chrome"
    created_at    = Column(DateTime,    server_default=func.now())
    updated_at    = Column(DateTime,    server_default=func.now(), onupdate=func.now())