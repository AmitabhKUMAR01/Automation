from pydantic import BaseModel, field_validator
from typing import Optional
from datetime import datetime
import json

VALID_PROCESSES = {
    "daily_linkedin_search",
    "daily_linkedin_connections",
    "daily_linkedin_acceptance_check",
    "daily_pitch_delivery",
    "daily_pitch_reply_check",
    "daily_followup_reply_check",
}

def check_allowed_processes(v: Optional[str]) -> Optional[str]:
    if v is None:
        return v
    try:
        processes = json.loads(v)
        if not isinstance(processes, list):
            raise ValueError("allowed_processes must be a JSON list")
        for p in processes:
            if p not in VALID_PROCESSES:
                raise ValueError(f"Invalid process: '{p}'. Allowed processes are: {', '.join(VALID_PROCESSES)}")
        return v
    except json.JSONDecodeError:
        raise ValueError("allowed_processes must be a valid JSON string")

class ProfileSettingBase(BaseModel):
    name: str
    max_connections_per_day: Optional[int] = 20
    max_messages_per_day: Optional[int] = 15
    allowed_processes: Optional[str] = "[]"
    profile_type: Optional[str] = "old"
    is_active: Optional[bool] = True
    location: Optional[str] = ""

    @field_validator('allowed_processes')
    @classmethod
    def validate_allowed_processes(cls, v):
        if v is None:
            return "[]"
        return check_allowed_processes(v)

class ProfileSettingCreate(ProfileSettingBase):
    pass

class ProfileSettingUpdate(BaseModel):
    name: Optional[str] = None
    max_connections_per_day: Optional[int] = None
    max_messages_per_day: Optional[int] = None
    allowed_processes: Optional[str] = None
    profile_type: Optional[str] = None
    is_active: Optional[bool] = None

    @field_validator('allowed_processes')
    @classmethod
    def validate_allowed_processes(cls, v):
        return check_allowed_processes(v)

class ProfileSettingResponse(ProfileSettingBase):
    id: int
    last_used_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
