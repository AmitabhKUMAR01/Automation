"""Resume profile schema used by match + compose."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ResumeProfile(BaseModel):
    full_name: str | None = None
    headline: str | None = None
    summary: str | None = None
    skills: list[str] = Field(default_factory=list)
    experience: list[str] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    years_experience: float | None = None
    raw_text: str = ""

    def facts_blob(self) -> str:
        """Compact, prompt-safe dump of resume facts only."""
        exp = [f"- {e}" for e in self.experience] if self.experience else ["- None listed"]
        edu = [f"- {e}" for e in self.education] if self.education else ["- None listed"]
        parts = [
            f"Name: {self.full_name or 'Unknown'}",
            f"Headline: {self.headline or 'Unknown'}",
            f"Years experience: {self.years_experience if self.years_experience is not None else 'Unknown'}",
            f"Skills: {', '.join(self.skills) if self.skills else 'None listed'}",
            f"Locations: {', '.join(self.locations) if self.locations else 'None listed'}",
            "Experience:",
            *exp,
            "Education:",
            *edu,
            "Summary:",
            self.summary or "(none)",
        ]
        return "\n".join(parts)
