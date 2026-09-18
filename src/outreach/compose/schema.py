"""Compose stage schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class ComposeDraft(BaseModel):
    subject: str
    body: str
    refusal: bool = False
    refusal_reason: str | None = None
    post_detail_referenced: str | None = Field(
        default=None,
        description="Specific detail from the post used in the email",
    )

    @field_validator("subject", "body", mode="before")
    @classmethod
    def _strip(cls, value: object) -> str:
        return str(value or "").strip()
