"""Parse stage helpers: LLM JSON validation + status transitions."""

from __future__ import annotations

from pathlib import Path

from outreach.config import reload_settings
from outreach.db.models import Post, PostStatus
from outreach.db.session import init_db, reset_engine, session_scope
from outreach.parse.emails import extract_emails
from outreach.parse.schema import ParsedPostFields
from outreach.parse.stage import apply_parsed_fields, parse_llm_json
from tests.fixtures_posts import MESSY_POSTS


def test_parse_llm_json_valid() -> None:
    fields = parse_llm_json(
        '{"role_title":"Backend Engineer","company_name":"Acme","location":"BLR",'
        '"experience_required":"3+ years","skills":["Python","FastAPI"],'
        '"contact_name":"Priya","contact_email":"priya@acme.io","application_method":"email"}'
    )
    assert fields.role_title == "Backend Engineer"
    assert fields.skills == ["Python", "FastAPI"]


def test_parse_llm_json_fenced_and_retry_shape() -> None:
    fields = parse_llm_json(
        '```json\n{"role_title":"SWE","company_name":null,"location":null,'
        '"experience_required":null,"skills":"Python, Redis","contact_name":null,'
        '"contact_email":null,"application_method":"linkedin"}\n```'
    )
    assert fields.skills == ["Python", "Redis"]
    assert fields.application_method == "linkedin"


def test_apply_parsed_sets_needs_enrichment_without_email(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "p.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db.as_posix()}")
    reset_engine()
    reload_settings()
    init_db()

    with session_scope() as session:
        post = Post(url="https://x/1", raw_text=MESSY_POSTS["no_email_dm_only"], status=PostStatus.ingested)
        session.add(post)
        session.flush()
        pid = post.id

    with session_scope() as session:
        post = session.get(Post, pid)
        assert post is not None
        fields = ParsedPostFields(
            role_title="Python Developer",
            company_name=None,
            application_method="linkedin",
        )
        apply_parsed_fields(post, fields, extract_emails(post.raw_text))
        assert post.status == PostStatus.needs_enrichment
        assert post.contact_email is None


def test_apply_parsed_prefers_regex_email(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "p2.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db.as_posix()}")
    reset_engine()
    reload_settings()
    init_db()

    text = MESSY_POSTS["obfuscated_at_dot"]
    with session_scope() as session:
        post = Post(url="https://x/2", raw_text=text, status=PostStatus.ingested)
        session.add(post)
        session.flush()
        pid = post.id

    with session_scope() as session:
        post = session.get(Post, pid)
        assert post is not None
        fields = ParsedPostFields(
            role_title="Software Engineer",
            company_name="BrightLabs",
            contact_email="wrong@example.com",  # LLM hallucination — regex wins
            application_method="email",
        )
        emails = extract_emails(text)
        apply_parsed_fields(post, fields, emails)
        assert post.contact_email == "jane@brightlabs.com"
        assert post.status == PostStatus.parsed
