"""Pydantic schemas for LLM parse output."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class ParsedPostFields(BaseModel):
    role_title: str | None = None
    company_name: str | None = None
    location: str | None = None
    experience_required: str | None = None
    skills: list[str] = Field(default_factory=list)
    contact_name: str | None = None
    contact_email: str | None = None
    application_method: str | None = Field(
        default=None,
        description="email | linkedin | form | other",
    )

    @field_validator("skills", mode="before")
    @classmethod
    def _coerce_skills(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [s.strip() for s in value.split(",") if s.strip()]
        if isinstance(value, list):
            return [str(s).strip() for s in value if str(s).strip()]
        return []

    @field_validator("contact_email", mode="before")
    @classmethod
    def _normalize_email(cls, value: object) -> str | None:
        if value is None or value == "":
            return None
        return str(value).strip().lower()
