import uuid
from sqlalchemy import Column, String, DateTime, Integer, func, Boolean, JSON
from app.config.database import Base

class Business_Client(Base):
    __tablename__ = "business_clients"
    id = Column(Integer, primary_key=True)
    uuid = Column(
        String(36),
        nullable=False,
        unique=True,
        index=True,
        default=lambda: str(uuid.uuid4()),
    )
    name = Column(String(255), nullable=False)
    category = Column(String(255), nullable=True)
    url = Column(String(255),nullable=True)
    phone = Column(String(20),nullable=True)
    email = Column(String(255), nullable=True)
    address = Column(String(500), nullable=True)
    is_approached = Column(Boolean(), default=False, nullable=False)
    is_audited    = Column(Boolean(), default=False, nullable=False)  # True once website_audit completed
    # LinkedIn enrichment tracking
    is_linkedin_searched   = Column(Boolean(), default=False, nullable=False)  # True once LinkedIn search ran (success or no-results)
    linkedin_search_status = Column(String(50), default="pending", nullable=True)  # pending | completed | no_contacts_found | company_not_found | session_expired | error
    scrape_source = Column(String(255), nullable=True)
    social_links = Column(JSON(), nullable=True)
    meta_data = Column(JSON(), nullable=True)
    country = Column(String(255), nullable=True)
    state = Column(String(255), nullable=True)
    city = Column(String(255), nullable=True)
    zip_code = Column(String(255), nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        onupdate=func.now(),
        nullable=True,
    )

