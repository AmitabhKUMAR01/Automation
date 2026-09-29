"""Connection outreach: targeting rules, templates, never-twice guarantee."""

from datetime import date
from pathlib import Path

from sqlmodel import select

from outreach.config import NetworkConfig, reload_settings
from outreach.db.models import ContactStatus, LinkedInContact, LinkedInMessageLog
from outreach.db.session import init_db, reset_engine, session_scope
from outreach.network.classify import (
    categorize,
    company_from_position,
    connected_days_ago,
    decide,
    first_name,
    mentions_excluded_company,
    years_from_experience,
)
from outreach.network.compose import compose_message

EXCL = ["hangingpanda", "hanging panda"]


def _decide(**kw):
    base = dict(
        headline=None, category=None, years=None, current_texts=[],
        excluded_companies=EXCL, min_years=4, profile_checked=True,
    )
    base.update(kw)
    return decide(**base)


def test_categorize_titles() -> None:
    assert categorize("Talent Acquisition Specialist at Zeta") == "hr"
    assert categorize("HR Manager | People Ops") == "hr"
    assert categorize("Co-Founder & CTO at Foo") == "leader"
    assert categorize("Senior Software Engineer @ Bar") == "senior"
    assert categorize("Software Engineer at Baz") is None


def test_excluded_company_all_spellings() -> None:
    for text in ("HR at HangingPanda Pvt Ltd", "Hanging Panda", "hanging-panda.com"):
        assert mentions_excluded_company(text, EXCL)
    assert not mentions_excluded_company("HR at Panda Express", EXCL)


def test_decide_rules() -> None:
    assert _decide(headline="HR at HangingPanda", category="hr")[0] is False
    assert _decide(category="hr", current_texts=["HR\nHangingPanda Pvt Ltd · Full-time\nJan 2022 - Present"])[0] is False
    assert _decide(category="hr")[0] is True
    assert _decide(category="leader")[0] is True
    assert _decide(category=None, years=5)[0] is True
    assert _decide(category=None, years=2)[0] is False
    assert _decide(category="senior", years=2)[0] is False
    assert _decide(category="senior", years=None)[0] is True


def test_profile_parsing() -> None:
    assert years_from_experience(["Dev\nAcme\nJan 2019 - Present"], today=date(2026, 9, 29)) == 7
    assert years_from_experience([]) is None
    assert company_from_position("Engineering Manager\nAcme Corp · Full-time\n2021 - Present") == "Acme Corp"


def test_connected_parsing_and_first_name() -> None:
    today = date(2026, 9, 29)
    assert connected_days_ago("Connected on September 25, 2026", today) == 4
    assert connected_days_ago("Connected 2 weeks ago", today) == 14
    assert first_name("Dr. Priya Sharma 🚀") == "Priya"
    assert first_name("🌟") == "there"


def test_templates_recent_vs_older() -> None:
    cfg = NetworkConfig()
    recent = compose_message(cfg, name="Ankit Singh", company="Vayuz", connected_days=3)
    older = compose_message(cfg, name="Ankit Singh", company=None, connected_days=200)
    assert recent.startswith("Hi Ankit, we just connected")
    assert "Vayuz" in recent
    assert "hope you're doing well" in older and "your organization" in older


def test_message_log_blocks_second_send(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    reload_settings()
    reset_engine()
    init_db()
    from outreach.network.stage import _record_sent

    with session_scope() as s:
        c = LinkedInContact(profile_url="https://www.linkedin.com/in/x/", name="X", status=ContactStatus.approved)
        s.add(c)
        s.flush()
        cid = c.id
    assert _record_sent(cid, "https://www.linkedin.com/in/x/", "hi") is True
    assert _record_sent(cid, "https://www.linkedin.com/in/x/", "hi") is False
    with session_scope() as s:
        assert len(s.exec(select(LinkedInMessageLog)).all()) == 1
    reset_engine()
