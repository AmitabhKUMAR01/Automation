from sqlalchemy import Column, String, Text, Integer, DateTime, func
from app.config.database import Base

class ScraperSetting(Base):
    __tablename__ = "scraper_settings"

    id = Column(Integer, primary_key=True)
    setting_key = Column(String(191), unique=True, nullable=False)
    setting_value = Column(Text, nullable=False) # Store JSON or comma-separated values
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
