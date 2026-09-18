"""Match stage schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class MatchResult(BaseModel):
    score: int = Field(ge=0, le=100)
    reasoning: str
    matched_skills: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)

    @field_validator("matched_skills", "gaps", mode="before")
    @classmethod
    def _listify(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [s.strip() for s in value.split(",") if s.strip()]
        if isinstance(value, list):
            return [str(s).strip() for s in value if str(s).strip()]
        return []
