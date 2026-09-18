"""Boundary DTOs and protocols shared across stages."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field, HttpUrl


class RawPost(BaseModel):
    """Normalized post from any ingest source before DB write."""

    url: str
    raw_text: str
    author_name: str | None = None
    author_profile_url: str | None = None
    posted_at: datetime | None = None
    source: str = "apify"
    extra: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class Source(Protocol):
    """Pluggable ingest source (Apify, RSS, CSV, …)."""

    name: str

    def fetch(self) -> list[RawPost]:
        """Return raw hiring posts. Must not write to the DB."""
        ...


class EmailFindResult(BaseModel):
    email: str
    confidence: float
    name: str | None = None
    title: str | None = None
    domain: str | None = None
    provider: str


@runtime_checkable
class EmailFinder(Protocol):
    """Pluggable contact email lookup (Hunter, Apollo, …)."""

    name: str

    def find(
        self,
        *,
        domain: str,
        company_name: str | None = None,
        target_titles: list[str] | None = None,
    ) -> EmailFindResult | None:
        ...


class LlmMessage(BaseModel):
    role: str
    content: str


class LlmResponse(BaseModel):
    content: str
    model: str
    raw: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class LlmClient(Protocol):
    """Thin LLM adapter — swap OpenAI / Anthropic / local without touching stages."""

    provider: str

    def complete(
        self,
        messages: list[LlmMessage],
        *,
        temperature: float = 0.2,
        response_format_json: bool = False,
    ) -> LlmResponse:
        ...


# Keep HttpUrl import used for future URL validation in sources
__all__ = [
    "RawPost",
    "Source",
    "EmailFindResult",
    "EmailFinder",
    "LlmMessage",
    "LlmResponse",
    "LlmClient",
    "HttpUrl",
]
