"""Prospect stages: search → review → paced connection requests → stats."""

from __future__ import annotations

import random
import time
from datetime import date, datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from sqlmodel import func, select

from outreach.config import ProspectsConfig, Settings, get_settings
from outreach.db.models import LinkedInContact, LinkedInProspect, ProspectStatus, utcnow
from outreach.db.session import session_scope
from outreach.ingest.sources.linkedin_feed import _dump_debug
from outreach.logging import get_logger
from outreach.network import prospects as pr
from outreach.network.browser import LinkedInBlocked
from outreach.network.classify import categorize, mentions_company_word, mentions_excluded_company
from outreach.network.stage import _open_context
from outreach.send.guards import within_business_hours
from outreach.stages import StageResult

log = get_logger("network.prospect_stage")
console = Console()

MAX_CONSECUTIVE_FAILURES = 3


def all_queries(cfg: ProspectsConfig) -> list[str]:
    return [f"{title} {loc}" for loc in cfg.locations for title in cfg.titles]


def queries_for_today(cfg: ProspectsConfig, today: date | None = None) -> list[str]:
    """Rotate through titles x locations so each run covers a different slice."""
    queries = all_queries(cfg)
    if not queries:
        return []
    n = max(1, min(cfg.max_queries_per_run, len(queries)))
    start = ((today or date.today()).toordinal() * n) % len(queries)
    return [queries[(start + i) % len(queries)] for i in range(n)]


def screen(
    result: dict, *, cfg: ProspectsConfig, excluded_companies: list[str]
) -> tuple[bool, str | None, str]:
    """(keep, category, reason) for one search result."""
    headline = result.get("headline") or ""
    location = (result.get("location") or "").lower()
    if result.get("degree") == "1st" or result.get("action") == "message":
        return False, None, "already connected"
    if result.get("action") == "pending":
        return False, None, "invite already pending"
    if mentions_excluded_company(headline, excluded_companies):
        return False, None, "excluded company"
    if cfg.location_aliases and not any(a in location for a in cfg.location_aliases):
        return False, None, f"location not in target ({result.get('location') or '?'})"
    category = categorize(headline)
    if not category or category not in cfg.allowed_categories:
        return False, category, f"title not in {cfg.allowed_categories}"
    big = mentions_company_word(headline, cfg.exclude_large_companies)
    if big:
        return False, category, f"large company / MNC ({big})"
    return True, category, "ok"


def rescreen_queue(settings: Settings) -> int:
    """Apply current rules to people already waiting, so config changes take effect."""
    net = settings.config.network
    dropped = 0
    with session_scope() as session:
        for p in session.exec(
            select(LinkedInProspect).where(
                LinkedInProspect.status.in_([ProspectStatus.pending_review, ProspectStatus.approved])  # type: ignore[attr-defined]
            )
        ).all():
            keep, category, reason = screen(
                {"degree": "2nd", "headline": p.headline, "location": p.location, "action": "connect"},
                cfg=net.prospects,
                excluded_companies=net.exclude_companies,
            )
            if not keep:
                p.status = ProspectStatus.excluded
                p.excluded_reason = reason
                p.updated_at = utcnow()
                session.add(p)
                dropped += 1
    return dropped


# --------------------------------------------------------------------------- search


def search(*, dry_run: bool = False) -> StageResult:
    settings = get_settings()
    net = settings.config.network
    cfg = net.prospects
    queries = queries_for_today(cfg)
    details = [f"queries={len(queries)}/{len(all_queries(cfg))}", f"network={cfg.network}"]
    details.extend(f"  q: {q}" for q in queries)
    if dry_run:
        return StageResult(stage="prospects-search", dry_run=True, details=details + ["dry-run: no browser"])
    details.append(f"rescreened_out={rescreen_queue(settings)}")

    with session_scope() as session:
        known = set(session.exec(select(LinkedInProspect.profile_url)).all())
        known |= set(session.exec(select(LinkedInContact.profile_url)).all())

    queued = excluded = 0
    with sync_playwright() as p:
        browser, context, state_path = _open_context(p, settings)
        page = context.new_page()
        page.set_default_timeout(settings.config.ingest.feed_navigation_timeout_ms)
        try:
            for q in queries:
                if queued >= cfg.max_new_per_run:
                    break
                for r in pr.search_people(page, keywords=q, network=cfg.network, max_pages=cfg.max_pages_per_query):
                    if r["profile_url"] in known:
                        continue
                    known.add(r["profile_url"])
                    keep, category, reason = screen(r, cfg=cfg, excluded_companies=net.exclude_companies)
                    with session_scope() as session:
                        session.add(
                            LinkedInProspect(
                                profile_url=r["profile_url"],
                                name=r["name"][:512],
                                headline=(r.get("headline") or "")[:1024] or None,
                                location=(r.get("location") or "")[:256] or None,
                                category=category,
                                search_query=q,
                                status=ProspectStatus.pending_review if keep else ProspectStatus.excluded,
                                excluded_reason=None if keep else reason,
                            )
                        )
                    if keep:
                        queued += 1
                    else:
                        excluded += 1
                page.wait_for_timeout(int(random.uniform(4.0, 8.0) * 1000))
        except LinkedInBlocked as exc:
            details.append(f"STOPPED: {exc} (debug={_dump_debug(page, settings)})")
            log.error("prospects_blocked", error=str(exc))
        finally:
            try:
                context.storage_state(path=str(state_path))
            except Exception:  # noqa: BLE001
                pass
            browser.close()

    details.append(f"queued_for_review={queued} excluded={excluded}")
    details.append("next: uv run pipeline prospects-review")
    return StageResult(stage="prospects-search", dry_run=False, processed=queued + excluded,
                       succeeded=queued, skipped=excluded, details=details)


# --------------------------------------------------------------------------- review


def _set_status(ids: list[int], status: ProspectStatus) -> None:
    with session_scope() as session:
        for pid in ids:
            p = session.get(LinkedInProspect, pid)
            if p:
                p.status = status
                p.reviewed_at = utcnow()
                p.updated_at = utcnow()
                session.add(p)


def review(*, dry_run: bool = False) -> StageResult:
    if not dry_run:
        rescreen_queue(get_settings())
    with session_scope() as session:
        items = [
            {"id": p.id, "name": p.name, "headline": p.headline, "location": p.location,
             "category": p.category, "query": p.search_query, "url": p.profile_url}
            for p in session.exec(
                select(LinkedInProspect)
                .where(LinkedInProspect.status == ProspectStatus.pending_review)
                .order_by(LinkedInProspect.id)
            ).all()
        ]
    details = [f"pending={len(items)}"]
    if not items:
        return StageResult(stage="prospects-review", dry_run=dry_run, details=details + ["nothing pending"])
    if dry_run:
        details.extend(f"#{i['id']} {i['name']} ({i['category']}) {i['location']}" for i in items[:20])
        return StageResult(stage="prospects-review", dry_run=True, processed=len(items), details=details)

    approved = rejected = 0
    for n, it in enumerate(items):
        console.print(
            Panel(
                f"[bold]{it['name']}[/bold]  ·  {it['category']}\n{it['headline'] or ''}\n"
                f"{it['location'] or ''}\nfound by: {it['query']}\n{it['url']}",
                title=f"Prospect #{it['id']}  ({n + 1}/{len(items)})",
                border_style="cyan",
            )
        )
        # a=approve r=reject s=skip A=approve this + all remaining q=quit
        choice = Prompt.ask("Action", choices=["a", "r", "s", "A", "q"], default="s", show_choices=True)
        if choice == "q":
            break
        if choice == "s":
            continue
        if choice == "r":
            _set_status([it["id"]], ProspectStatus.skipped)
            rejected += 1
            continue
        if choice == "A":
            rest = [x["id"] for x in items[n:]]
            _set_status(rest, ProspectStatus.approved)
            approved += len(rest)
            details.append(f"approved all remaining ({len(rest)})")
            break
        _set_status([it["id"]], ProspectStatus.approved)
        approved += 1

    details.append(f"approved={approved} rejected={rejected}")
    details.append("next: uv run pipeline prospects-invite")
    return StageResult(stage="prospects-review", dry_run=False, processed=len(items),
                       succeeded=approved, skipped=rejected, details=details)


# --------------------------------------------------------------------------- invite


def _invites_since(start_utc: datetime) -> int:
    with session_scope() as session:
        return int(
            session.exec(
                select(func.count()).select_from(LinkedInProspect).where(LinkedInProspect.invited_at >= start_utc)
            ).one()
            or 0
        )


def invite_budget(settings: Settings) -> tuple[int, int, int]:
    """(invites_today, invites_last_7_days, remaining_now)."""
    cfg = settings.config.network.prospects
    tz = ZoneInfo(settings.config.send.timezone)
    start_today = datetime.combine(datetime.now(tz).date(), dtime(0, 0), tzinfo=tz).astimezone(timezone.utc)
    today = _invites_since(start_today)
    week = _invites_since(datetime.now(timezone.utc) - timedelta(days=7))
    remaining = max(0, min(cfg.daily_cap - today, cfg.weekly_cap - week))
    return today, week, remaining


def _mark(pid: int, status: ProspectStatus, *, error: str | None = None, invited: bool = False) -> None:
    with session_scope() as session:
        p = session.get(LinkedInProspect, pid)
        if p:
            p.status = status
            p.last_error = error[:2000] if error else None
            if invited:
                p.invited_at = utcnow()
            p.updated_at = utcnow()
            session.add(p)


def invite(*, dry_run: bool = False, force: bool = False) -> StageResult:
    settings = get_settings()
    cfg = settings.config.network.prospects
    today, week, remaining = invite_budget(settings)
    details = [f"daily_cap={cfg.daily_cap} today={today}", f"weekly_cap={cfg.weekly_cap} last_7d={week}",
               f"remaining={remaining}"]

    ok_hours, why = within_business_hours(settings)
    if not ok_hours and not force:
        return StageResult(stage="prospects-invite", dry_run=dry_run, details=details + [f"blocked: {why}"])
    if remaining == 0:
        return StageResult(stage="prospects-invite", dry_run=dry_run, details=details + ["cap reached"])
    if not dry_run:
        rescreen_queue(settings)

    with session_scope() as session:
        queue = [
            {"id": p.id, "name": p.name, "url": p.profile_url}
            for p in session.exec(
                select(LinkedInProspect)
                .where(LinkedInProspect.status == ProspectStatus.approved)
                .order_by(LinkedInProspect.reviewed_at, LinkedInProspect.id)
            ).all()
        ][:remaining]
    details.append(f"queue={len(queue)}")
    if not queue:
        return StageResult(stage="prospects-invite", dry_run=dry_run, details=details + ["nothing approved"])
    if dry_run:
        details.extend(f"would invite {q['name']} {q['url']}" for q in queue)
        return StageResult(stage="prospects-invite", dry_run=True, processed=len(queue), details=details)

    invited = skipped = failed = 0
    consecutive = 0
    with sync_playwright() as p:
        browser, context, state_path = _open_context(p, settings)
        page = context.new_page()
        page.set_default_timeout(settings.config.ingest.feed_navigation_timeout_ms)
        try:
            for i, item in enumerate(queue):
                if not force and not within_business_hours(settings)[0]:
                    details.append("stopped: business hours ended")
                    break
                try:
                    outcome = pr.send_invite(page, item["url"])
                except LinkedInBlocked:
                    raise
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    consecutive += 1
                    _mark(item["id"], ProspectStatus.failed, error=str(exc))
                    log.warning("prospect_invite_failed", name=item["name"], error=str(exc).splitlines()[0],
                                debug=str(_dump_debug(page, settings)))
                    if consecutive >= MAX_CONSECUTIVE_FAILURES:
                        details.append("stopped: repeated failures (LinkedIn UI may have changed)")
                        break
                    continue
                consecutive = 0
                if outcome == "invited":
                    _mark(item["id"], ProspectStatus.invited, invited=True)
                    invited += 1
                    log.info("prospect_invited", name=item["name"])
                    if i < len(queue) - 1:
                        delay = random.uniform(cfg.delay_seconds_min, cfg.delay_seconds_max)
                        log.info("prospect_sleep", seconds=round(delay, 1))
                        time.sleep(delay)
                else:
                    _mark(item["id"], ProspectStatus.skipped, error=outcome)
                    skipped += 1
                    log.info("prospect_skipped", name=item["name"], reason=outcome)
        except LinkedInBlocked as exc:
            details.append(f"STOPPED: {exc} (debug={_dump_debug(page, settings)}) — pause invites for a week")
            log.error("prospects_invite_blocked", error=str(exc))
        finally:
            try:
                context.storage_state(path=str(state_path))
            except Exception:  # noqa: BLE001
                pass
            browser.close()

    details.append(f"invited={invited} skipped={skipped} failed={failed}")
    return StageResult(stage="prospects-invite", dry_run=False, processed=invited + skipped + failed,
                       succeeded=invited, skipped=skipped, failed=failed, details=details)


# --------------------------------------------------------------------------- accepted + stats


def mark_accepted(profile_urls: set[str]) -> int:
    """Called by connections-sync: invited prospects now in your connections list."""
    if not profile_urls:
        return 0
    n = 0
    with session_scope() as session:
        for p in session.exec(
            select(LinkedInProspect).where(LinkedInProspect.status == ProspectStatus.invited)
        ).all():
            if p.profile_url in profile_urls:
                p.status = ProspectStatus.accepted
                p.accepted_at = utcnow()
                p.updated_at = utcnow()
                session.add(p)
                n += 1
    return n


def stats() -> StageResult:
    settings = get_settings()
    with session_scope() as session:
        rows = session.exec(
            select(LinkedInProspect.status, func.count()).group_by(LinkedInProspect.status)
        ).all()
    counts = {str(getattr(s, "value", s)): int(c) for s, c in rows}
    sent_total = counts.get("invited", 0) + counts.get("accepted", 0)
    rate = (counts.get("accepted", 0) / sent_total * 100) if sent_total else 0.0
    today, week, remaining = invite_budget(settings)
    details = [f"{k}={v}" for k, v in sorted(counts.items())]
    details.append(f"acceptance_rate={rate:.0f}% ({counts.get('accepted', 0)}/{sent_total})")
    details.append(f"invites_today={today} last_7d={week} remaining_now={remaining}")
    if sent_total >= 20 and rate < 25:
        details.append("warning: acceptance below 25% — tighten titles/locations before inviting more")
    return StageResult(stage="prospects-stats", dry_run=False, details=details)
