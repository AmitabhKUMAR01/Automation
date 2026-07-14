from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, func
from app.config.database import Base

class ScraperJob(Base):
    __tablename__ = "scraper_jobs"

    id = Column(Integer, primary_key=True)
    category = Column(String(255), nullable=False)
    city = Column(String(255), nullable=False)
    state = Column(String(255), nullable=True)
    country = Column(String(255), nullable=True)
    lat = Column(Float, nullable=True)
    long = Column(Float, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
