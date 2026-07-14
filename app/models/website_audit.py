import uuid
from sqlalchemy import Column, String, DateTime, Integer, Float, Boolean, JSON, ForeignKey, Text, func
from app.config.database import Base


class WebsiteAudit(Base):
    __tablename__ = "website_audits"

    id = Column(Integer, primary_key=True)
    uuid = Column(
        String(36),
        nullable=False,
        unique=True,
        index=True,
        default=lambda: str(uuid.uuid4()),
    )
    business_client_id = Column(Integer, ForeignKey("business_clients.id"), nullable=False, index=True)
    url = Column(Text, nullable=True)
    status = Column(String(50), default="pending", nullable=False)   # pending | running | completed | failed
    error_message = Column(Text, nullable=True)
    http_status_code = Column(Integer, nullable=True)
    is_https = Column(Boolean, nullable=True)
    redirected_url = Column(Text, nullable=True)
    load_time_ms = Column(Float, nullable=True)              # window.performance: loadEventEnd - navigationStart
    dom_content_loaded_ms = Column(Float, nullable=True)     # domContentLoadedEventEnd - navigationStart
    first_paint_ms = Column(Float, nullable=True)            # from PerformancePaintTiming API
    broken_links_count = Column(Integer, default=0, nullable=True)
    broken_images_count = Column(Integer, default=0, nullable=True)
    broken_links_detail = Column(JSON, nullable=True)

    # ---------- SEO ----------
    seo_title = Column(Text, nullable=True)
    seo_description = Column(Text, nullable=True)
    seo_keywords = Column(Text, nullable=True)
    has_h1 = Column(Boolean, nullable=True)
    has_canonical = Column(Boolean, nullable=True)
    og_title = Column(Text, nullable=True)
    og_description = Column(Text, nullable=True)
    og_image = Column(Text, nullable=True) 
    raw_seo_tags = Column(JSON, nullable=True)             

    # ---------- Accessibility ----------
    missing_alt_images_count = Column(Integer, default=0, nullable=True)
    missing_label_inputs_count = Column(Integer, default=0, nullable=True)
    has_lang_attr = Column(Boolean, nullable=True)
    accessibility_issues = Column(JSON, nullable=True)

    # ---------- Console Issues ----------
    console_errors_count = Column(Integer, default=0, nullable=True)
    console_warnings_count = Column(Integer, default=0, nullable=True)

    # ---------- Lead Scoring ----------
    is_scored = Column(Boolean, default=False, nullable=False) 
    console_logs = Column(JSON, nullable=True) 

    # ---------- Timestamps ----------
    audited_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
