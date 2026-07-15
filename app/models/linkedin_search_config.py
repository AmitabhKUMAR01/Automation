from sqlalchemy import Column, Integer, String, Boolean, DateTime, func, JSON
from app.config.database import Base

class LinkedinSearchConfig(Base):
    __tablename__ = "linkedin_search_configs"

    id = Column(Integer, primary_key=True, index=True)
    
    # Store positions as a JSON array
    positions = Column(JSON)
    
    # Store location
    location = Column(String(255))
    
    # Store schedule time
    schedule_time = Column(String(50))
    timezone = Column(String(50))
    
    is_active = Column(Boolean, default=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
