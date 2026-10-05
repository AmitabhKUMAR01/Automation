"""Prospect search screening, query rotation, invite budget."""

from datetime import date, timedelta
from pathlib import Path

from outreach.config import ProspectsConfig, reload_settings
from outreach.db.models import LinkedInProspect, ProspectStatus, utcnow
from outreach.db.session import init_db, reset_engine, session_scope
from outreach.network.prospect_stage import (
    all_queries,
    invite_budget,
    mark_accepted,
    queries_for_today,
    screen,
)
from outreach.network.prospects import parse_result_lines, search_url

EXCL = ["hangingpanda", "hanging panda"]
CFG = ProspectsConfig()


def _r(**kw):
    base = {"degree": "2nd", "headline": "Talent Acquisition at Zeta", "location": "Noida, Uttar Pradesh", "action": "connect"}
    base.update(kw)
    return base


def test_parse_result_lines() -> None:
    lines = ["Priya Sharma", "View Priya Sharma's profile", "• 2nd", "2nd degree connection",
             "HR Manager at Acme", "Gurugram, Haryana, India", "Connect"]
    got = parse_result_lines(lines, "Priya Sharma")
    assert got["degree"] == "2nd"
    assert got["headline"] == "HR Manager at Acme"
    assert got["location"] == "Gurugram, Haryana, India"
    assert got["action"] == "connect"


def test_screen_hr_only_and_no_mnc() -> None:
    cfg = ProspectsConfig(exclude_large_companies=["TCS", "IBM", "Tech Mahindra"])
    assert screen(_r(headline="CTO at Foo"), cfg=cfg, excluded_companies=EXCL)[0] is False
    assert screen(_r(headline="Senior Engineer at Foo"), cfg=cfg, excluded_companies=EXCL)[0] is False
    assert screen(_r(headline="HR Recruiter at TCS"), cfg=cfg, excluded_companies=EXCL)[2].startswith("large company")
    assert screen(_r(headline="Talent Acquisition | Tech Mahindra"), cfg=cfg, excluded_companies=EXCL)[0] is False
    # whole-word: NIBM must not match IBM
    assert screen(_r(headline="HR at NIBM Solutions"), cfg=cfg, excluded_companies=EXCL)[0] is True
    assert screen(_r(headline="HR at Qwerty Labs"), cfg=cfg, excluded_companies=EXCL) == (True, "hr", "ok")


def test_screen_rules() -> None:
    assert screen(_r(), cfg=CFG, excluded_companies=EXCL) == (True, "hr", "ok")
    wide = ProspectsConfig(allowed_categories=["hr", "leader"])
    assert screen(_r(headline="CTO at Foo", location="Delhi, India"), cfg=wide, excluded_companies=EXCL)[1] == "leader"
    assert screen(_r(location="Bengaluru, Karnataka"), cfg=CFG, excluded_companies=EXCL)[0] is False
    assert screen(_r(headline="HR at HangingPanda"), cfg=CFG, excluded_companies=EXCL)[0] is False
    assert screen(_r(action="pending"), cfg=CFG, excluded_companies=EXCL)[2] == "invite already pending"
    assert screen(_r(degree="1st"), cfg=CFG, excluded_companies=EXCL)[2] == "already connected"
    assert screen(_r(headline="Student at DU"), cfg=CFG, excluded_companies=EXCL)[0] is False


def test_query_rotation_covers_everything() -> None:
    total = all_queries(CFG)
    assert len(total) == len(CFG.titles) * len(CFG.locations)
    seen: set[str] = set()
    for d in range(len(total)):
        seen.update(queries_for_today(CFG, date(2026, 1, 1) + timedelta(days=d)))
    assert seen == set(total)


def test_search_url_second_degree() -> None:
    url = search_url("HR Noida", ["S"], 2)
    assert "network=%5B%22S%22%5D" in url and "page=2" in url and "keywords=HR+Noida" in url


def test_budget_and_accept(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'p.db').as_posix()}")
    settings = reload_settings()
    reset_engine()
    init_db()
    with session_scope() as s:
        for i in range(3):
            s.add(LinkedInProspect(profile_url=f"https://www.linkedin.com/in/p{i}/", name=f"P{i}",
                                   status=ProspectStatus.invited, invited_at=utcnow()))
    today, week, remaining = invite_budget(settings)
    assert (today, week) == (3, 3)
    assert remaining == settings.config.network.prospects.daily_cap - 3
    assert mark_accepted({"https://www.linkedin.com/in/p1/"}) == 1
    reset_engine()
