"""Load resume PDF/text once, cache structured profile in SQLite."""

from __future__ import annotations

import json
import re
from pathlib import Path

from pypdf import PdfReader
from sqlmodel import select

from outreach.config import Settings, get_settings
from outreach.db.models import ResumeCache, utcnow
from outreach.db.session import session_scope
from outreach.llm import get_llm_client
from outreach.logging import get_logger
from outreach.match.profile import ResumeProfile
from outreach.protocols import LlmMessage

log = get_logger("resume")

PROFILE_SYSTEM = """Extract a structured resume profile as JSON with keys:
full_name, headline, summary, skills (array), experience (array of short bullet strings),
education (array of short strings), locations (array), years_experience (number or null).
Use only facts present in the resume text. Do not invent employers or skills."""


def extract_pdf_text(path: Path) -> str:
    reader = PdfReader(str(path))
    chunks: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        chunks.append(text)
    return "\n".join(chunks).strip()


def extract_resume_text(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(
            f"Resume not found at {path}. Place your PDF there or set match.resume_path in config.yaml."
        )
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        text = extract_pdf_text(path)
    elif suffix in {".txt", ".md"}:
        text = path.read_text(encoding="utf-8")
    else:
        raise ValueError(f"Unsupported resume format: {suffix} (use .pdf or .txt)")
    if len(text.strip()) < 40:
        raise ValueError(f"Resume text too short or unreadable: {path}")
    return text.strip()


def _parse_profile_json(content: str, raw_text: str) -> ResumeProfile:
    content = content.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", content, re.DOTALL | re.IGNORECASE)
    if fence:
        content = fence.group(1).strip()
    data = json.loads(content)
    profile = ResumeProfile.model_validate(data)
    profile.raw_text = raw_text
    return profile


def structure_profile(raw_text: str, *, settings: Settings | None = None) -> ResumeProfile:
    settings = settings or get_settings()
    client = get_llm_client(settings)
    response = client.complete(
        [
            LlmMessage(role="system", content=PROFILE_SYSTEM),
            LlmMessage(role="user", content=raw_text[:20000]),
        ],
        temperature=0.1,
        response_format_json=True,
    )
    try:
        return _parse_profile_json(response.content, raw_text)
    except Exception:
        # Fallback: raw text only so match/compose can still run
        log.warning("resume_structure_failed_using_raw")
        return ResumeProfile(raw_text=raw_text, summary=raw_text[:1500])


def load_or_build_profile(
    settings: Settings | None = None,
    *,
    force_refresh: bool = False,
    dry_run: bool = False,
) -> ResumeProfile:
    settings = settings or get_settings()
    path = settings.resolve_path(settings.config.match.resume_path)
    # Allow .txt sibling if PDF missing (local/dev)
    if not path.exists() and path.suffix.lower() == ".pdf":
        txt = path.with_suffix(".txt")
        if txt.exists():
            path = txt

    raw_text = extract_resume_text(path)
    mtime = path.stat().st_mtime
    source = str(path)

    if dry_run:
        return ResumeProfile(
            raw_text=raw_text[:2000],
            summary="(dry-run stub profile)",
            skills=[],
        )

    if not force_refresh:
        with session_scope() as session:
            cached = session.exec(
                select(ResumeCache)
                .where(ResumeCache.source_path == source)
                .where(ResumeCache.source_mtime == mtime)
            ).first()
            if cached is not None:
                profile = ResumeProfile.model_validate(cached.profile_json)
                profile.raw_text = cached.raw_text
                log.info("resume_cache_hit", path=source)
                return profile

    profile = structure_profile(raw_text, settings=settings)
    with session_scope() as session:
        # drop stale rows for this path
        old = session.exec(select(ResumeCache).where(ResumeCache.source_path == source)).all()
        for row in old:
            session.delete(row)
        session.add(
            ResumeCache(
                source_path=source,
                source_mtime=mtime,
                raw_text=raw_text,
                profile_json=json.loads(profile.model_dump_json(exclude={"raw_text"})),
                parsed_at=utcnow(),
            )
        )
    log.info("resume_cache_store", path=source, skills=len(profile.skills))
    return profile
