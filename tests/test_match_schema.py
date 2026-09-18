"""Match schema + resume text loading."""

from __future__ import annotations

from pathlib import Path

from outreach.match.profile import ResumeProfile
from outreach.match.resume import extract_resume_text
from outreach.match.schema import MatchResult


def test_match_result_bounds() -> None:
    m = MatchResult(
        score=72,
        reasoning="Solid Python overlap",
        matched_skills=["Python", "PostgreSQL"],
        gaps=["Kubernetes"],
    )
    assert m.score == 72
    assert "Python" in m.matched_skills


def test_facts_blob_includes_skills() -> None:
    blob = ResumeProfile(
        full_name="Amitabh",
        skills=["Python", "NestJS"],
        experience=["Built APIs"],
    ).facts_blob()
    assert "Python" in blob
    assert "Built APIs" in blob


def test_extract_sample_resume_txt() -> None:
    path = Path(__file__).resolve().parents[1] / "resume" / "resume.txt"
    text = extract_resume_text(path)
    assert "React" in text or "Full-Stack" in text
    assert len(text) > 40
