from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, func
from sqlalchemy.dialects.sqlite import JSON
from app.config.database import Base

class ProfileSetting(Base):
    __tablename__ = "profile_settings"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), unique=True, index=True, nullable=False)
    
    # Limits
    max_connections_per_day = Column(Integer, default=20)
    max_messages_per_day = Column(Integer, default=15)
    
    # Configuration
    # Store as a JSON string list like: ["daily_linkedin_search", "daily_linkedin_connections"]
    allowed_processes = Column(Text, default="[]")
    
    # 'new' or 'old' to dictate different behaviors/delays
    profile_type = Column(String(50), default="old")
    
    # Playwright session state JSON dictionary dumped as string
    session_state = Column(Text, nullable=True)
    
    # Status
    is_active = Column(Boolean, default=True)
    last_used_at = Column(DateTime, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
