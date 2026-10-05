"""Connection outreach stages: sync (scrape+classify+draft) → review → paced send."""

from __future__ import annotations

import random
import time
from datetime import datetime, time as dtime, timezone
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, func, select

from outreach.config import Settings, get_settings
from outreach.db.models import ContactStatus, LinkedInContact, LinkedInMessageLog, utcnow
from outreach.db.session import session_scope
from outreach.ingest.sources.linkedin_feed import _dump_debug, _launch_browser, _storage_path
from outreach.logging import get_logger
from outreach.network import browser as li
from outreach.network.classify import (
    categorize,
    company_from_position,
    connected_days_ago,
    current_positions,
    decide,
    mentions_excluded_company,
    years_from_experience,
)
from outreach.network.compose import compose_message
from outreach.send.guards import within_business_hours
from outreach.stages import StageResult

log = get_logger("network")
console = Console()

MAX_CONSECUTIVE_FAILURES = 3


def _open_context(p, settings: Settings):
    state_path = _storage_path(settings)
    if not state_path.exists():
        raise FileNotFoundError(
            f"LinkedIn session not found at {state_path}. Run: uv run pipeline linkedin-login"
        )
    browser = _launch_browser(p, headless=settings.config.network.headless)
    context = browser.new_context(
        storage_state=str(state_path),
        viewport={"width": 1280, "height": 900},
        locale="en-US",
    )
    return browser, context, state_path


# --------------------------------------------------------------------------- sync


def _upsert_connections(cards: list[dict], excluded: list[str]) -> tuple[int, int]:
    inserted = pre_excluded = 0
    with session_scope() as session:
        known = set(session.exec(select(LinkedInContact.profile_url)).all())
        for card in cards:
            url = card["profile_url"]
            if url in known:
                continue
            headline = card.get("headline")
            contact = LinkedInContact(
                profile_url=url,
                name=(card.get("name") or "").strip()[:512],
                headline=(headline or None) and headline[:1024],
                connected_text=card.get("connected"),
                connected_days=connected_days_ago(card.get("connected")),
                category=categorize(headline),
            )
            if mentions_excluded_company(headline, excluded):
                contact.status = ContactStatus.excluded
                contact.excluded_reason = "excluded company in headline"
                pre_excluded += 1
            session.add(contact)
            known.add(url)
            inserted += 1
    return inserted, pre_excluded


def _contacts_to_verify(limit: int) -> list[dict]:
    with session_scope() as session:
        rows = session.exec(
            select(LinkedInContact)
            .where(LinkedInContact.status == ContactStatus.discovered)
            .where(col(LinkedInContact.profile_checked).is_(False))
        ).all()
        # Recent connections first ("we just connected" is only true for a while),
        # then people whose title already makes them a target.
        rows = sorted(
            rows,
            key=lambda c: (
                c.connected_days if c.connected_days is not None else 10_000,
                0 if c.category else 1,
            ),
        )
        return [
            {
                "id": c.id,
                "profile_url": c.profile_url,
                "name": c.name,
                "headline": c.headline,
                "category": c.category,
                "connected_days": c.connected_days,
            }
            for c in rows[:limit]
        ]


def _apply_verification(settings: Settings, item: dict, positions: list[str]) -> str:
    cfg = settings.config.network
    current = current_positions(positions)
    years = years_from_experience(positions)
    company = next((c for c in (company_from_position(t) for t in current) if c), None)
    eligible, category, reason = decide(
        headline=item["headline"],
        category=item["category"],
        years=years,
        current_texts=current,
        excluded_companies=cfg.exclude_companies,
        min_years=cfg.min_years,
        profile_checked=True,
    )
    with session_scope() as session:
        c = session.get(LinkedInContact, item["id"])
        if c is None:
            return "missing"
        c.profile_checked = True
        c.years_experience = years
        c.company = company
        c.category = category
        c.updated_at = utcnow()
        if eligible:
            c.status = ContactStatus.pending_review
            c.excluded_reason = None
            c.message = compose_message(
                cfg, name=c.name, company=company, connected_days=c.connected_days
            )
        else:
            c.status = ContactStatus.excluded
            c.excluded_reason = reason
        session.add(c)
    return "queued" if eligible else f"excluded: {reason}"


def sync(*, dry_run: bool = False) -> StageResult:
    settings = get_settings()
    cfg = settings.config.network
    details = [
        f"max_scan={cfg.max_connections_scan}",
        f"max_profile_checks={cfg.max_profile_checks}",
        f"exclude={cfg.exclude_companies}",
        f"min_years={cfg.min_years}",
    ]
    if dry_run:
        return StageResult(stage="connections-sync", dry_run=True, details=details + ["dry-run: no browser"])

    queued = excluded = failed = 0
    with sync_playwright() as p:
        browser, context, state_path = _open_context(p, settings)
        page = context.new_page()
        page.set_default_timeout(settings.config.ingest.feed_navigation_timeout_ms)
        try:
            cards = li.scrape_connections(page, max_items=cfg.max_connections_scan)
            inserted, pre_excluded = _upsert_connections(cards, cfg.exclude_companies)
            details.append(f"scanned={len(cards)} new={inserted} excluded_by_headline={pre_excluded}")
            from outreach.network.prospect_stage import mark_accepted

            accepted = mark_accepted({c["profile_url"] for c in cards})
            if accepted:
                details.append(f"invites_accepted={accepted}")

            for item in _contacts_to_verify(cfg.max_profile_checks):
                try:
                    positions = li.read_experience(page, item["profile_url"])
                    outcome = _apply_verification(settings, item, positions)
                    log.info("network_profile_checked", name=item["name"], outcome=outcome)
                    if outcome == "queued":
                        queued += 1
                    else:
                        excluded += 1
                except li.LinkedInBlocked:
                    raise
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    log.warning("network_profile_failed", name=item["name"], error=str(exc))
                page.wait_for_timeout(int(random.uniform(3.0, 7.0) * 1000))
        except li.LinkedInBlocked as exc:
            debug = _dump_debug(page, settings)
            details.append(f"STOPPED: {exc} (debug={debug})")
            log.error("network_blocked", error=str(exc))
            failed += 1
        finally:
            try:
                context.storage_state(path=str(state_path))
            except Exception:  # noqa: BLE001
                pass
            browser.close()

    details.append(f"queued_for_review={queued} excluded={excluded} failed={failed}")
    details.append("next: uv run pipeline connections-review")
    return StageResult(
        stage="connections-sync",
        dry_run=False,
        processed=queued + excluded + failed,
        succeeded=queued,
        skipped=excluded,
        failed=failed,
        details=details,
    )


# --------------------------------------------------------------------------- review


def _set_status(contact_id: int, status: ContactStatus, *, message: str | None = None) -> None:
    with session_scope() as session:
        c = session.get(LinkedInContact, contact_id)
        if c is None:
            return
        c.status = status
        if message is not None:
            c.message = message
        c.reviewed_at = utcnow()
        c.updated_at = utcnow()
        session.add(c)


def review(*, dry_run: bool = False) -> StageResult:
    with session_scope() as session:
        items = [
            {
                "id": c.id,
                "name": c.name,
                "headline": c.headline,
                "company": c.company,
                "category": c.category,
                "years": c.years_experience,
                "connected": c.connected_text,
                "url": c.profile_url,
                "message": c.message or "",
            }
            for c in session.exec(
                select(LinkedInContact)
                .where(LinkedInContact.status == ContactStatus.pending_review)
                .order_by(LinkedInContact.id)
            ).all()
        ]

    details = [f"pending={len(items)}"]
    if not items:
        return StageResult(stage="connections-review", dry_run=dry_run, details=details + ["nothing pending"])
    if dry_run:
        details.extend(f"#{it['id']} {it['name']} ({it['category']}) {it['company']}" for it in items[:15])
        return StageResult(stage="connections-review", dry_run=True, processed=len(items), details=details)

    approved = rejected = edited = 0
    for it in items:
        console.print(
            Panel(
                f"[bold]{it['name']}[/bold]  ·  {it['category']}  ·  {it['years'] or '?'} yrs\n"
                f"{it['headline'] or ''}\nCompany: {it['company'] or '?'}\n"
                f"{it['connected'] or ''}\n{it['url']}\n\n[green]{it['message']}[/green]",
                title=f"Contact #{it['id']}",
                border_style="cyan",
            )
        )
        # a=approve e=edit r=reject(never message) s=skip(decide later) q=quit
        choice = Prompt.ask("Action", choices=["a", "e", "r", "s", "q"], default="s", show_choices=True)
        if choice == "q":
            details.append("quit")
            break
        if choice == "s":
            continue
        if choice == "r":
            _set_status(it["id"], ContactStatus.skipped)
            rejected += 1
            continue
        if choice == "e":
            new_msg = Prompt.ask("Message", default=it["message"]).strip() or it["message"]
            _set_status(it["id"], ContactStatus.approved, message=new_msg)
            edited += 1
            approved += 1
            continue
        _set_status(it["id"], ContactStatus.approved)
        approved += 1

    details.append(f"approved={approved} rejected={rejected} edited={edited}")
    details.append("next: uv run pipeline connections-send")
    return StageResult(
        stage="connections-review",
        dry_run=False,
        processed=len(items),
        succeeded=approved,
        skipped=rejected,
        details=details,
    )


# --------------------------------------------------------------------------- send


def messages_sent_today(settings: Settings) -> int:
    tz = ZoneInfo(settings.config.send.timezone)
    start = datetime.combine(datetime.now(tz).date(), dtime(0, 0), tzinfo=tz).astimezone(timezone.utc)
    with session_scope() as session:
        return int(
            session.exec(
                select(func.count()).select_from(LinkedInMessageLog).where(LinkedInMessageLog.sent_at >= start)
            ).one()
            or 0
        )


def _record_sent(contact_id: int, profile_url: str, message: str) -> bool:
    """False if the UNIQUE log row already existed (message went out before)."""
    try:
        with session_scope() as session:
            session.add(LinkedInMessageLog(contact_id=contact_id, profile_url=profile_url, message=message))
            c = session.get(LinkedInContact, contact_id)
            if c:
                c.status = ContactStatus.sent
                c.sent_at = utcnow()
                c.updated_at = utcnow()
                session.add(c)
        return True
    except IntegrityError:
        _set_status(contact_id, ContactStatus.sent)
        return False


def _mark(contact_id: int, status: ContactStatus, error: str) -> None:
    with session_scope() as session:
        c = session.get(LinkedInContact, contact_id)
        if c:
            c.status = status
            c.last_error = error[:2000]
            c.updated_at = utcnow()
            session.add(c)


def _requeue_failed() -> int:
    with session_scope() as session:
        rows = session.exec(
            select(LinkedInContact).where(LinkedInContact.status == ContactStatus.failed)
        ).all()
        for c in rows:
            c.status = ContactStatus.approved
            c.last_error = None
            c.updated_at = utcnow()
            session.add(c)
        return len(rows)


def send(*, dry_run: bool = False, force: bool = False, retry_failed: bool = False) -> StageResult:
    settings = get_settings()
    cfg = settings.config.network
    if retry_failed and not dry_run:
        log.info("network_requeued_failed", count=_requeue_failed())
    today = messages_sent_today(settings)
    remaining = max(0, cfg.daily_cap - today)
    details = [f"daily_cap={cfg.daily_cap}", f"sent_today={today}", f"remaining={remaining}"]

    ok_hours, why = within_business_hours(settings)
    if not ok_hours and not force:
        return StageResult(stage="connections-send", dry_run=dry_run, details=details + [f"blocked: {why}"])
    if remaining == 0:
        return StageResult(stage="connections-send", dry_run=dry_run, details=details + ["daily cap reached"])

    with session_scope() as session:
        already = set(session.exec(select(LinkedInMessageLog.profile_url)).all())
        queue = [
            {"id": c.id, "name": c.name, "url": c.profile_url, "message": c.message or ""}
            for c in session.exec(
                select(LinkedInContact)
                .where(LinkedInContact.status == ContactStatus.approved)
                .order_by(LinkedInContact.reviewed_at, LinkedInContact.id)
            ).all()
            if c.profile_url not in already and c.message
        ][:remaining]

    details.append(f"queue={len(queue)}")
    if not queue:
        return StageResult(stage="connections-send", dry_run=dry_run, details=details + ["nothing approved"])
    if dry_run:
        # Windows consoles (cp1252) choke on emoji in the template
        details.extend(
            f"would message {q['name']} -> "
            f"{q['message'][:70].encode('ascii', 'ignore').decode()}..."
            for q in queue
        )
        return StageResult(stage="connections-send", dry_run=True, processed=len(queue), details=details)

    sent = skipped = failed = 0
    consecutive_failures = 0
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
                    outcome = li.send_message(page, item["url"], item["message"])
                except li.LinkedInBlocked:
                    raise
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    consecutive_failures += 1
                    _mark(item["id"], ContactStatus.failed, str(exc))
                    log.warning(
                        "network_send_failed",
                        name=item["name"],
                        url=page.url,
                        error=str(exc).splitlines()[0],
                        debug=str(_dump_debug(page, settings)),
                    )
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        details.append("stopped: repeated failures (LinkedIn UI may have changed)")
                        break
                    continue

                consecutive_failures = 0
                if outcome == "sent":
                    _record_sent(item["id"], item["url"], item["message"])
                    sent += 1
                    log.info("network_message_sent", name=item["name"])
                else:
                    _mark(item["id"], ContactStatus.skipped, outcome)
                    skipped += 1
                    log.info("network_message_skipped", name=item["name"], reason=outcome)
                    continue

                if i < len(queue) - 1:
                    delay = random.uniform(cfg.delay_seconds_min, cfg.delay_seconds_max)
                    log.info("network_send_sleep", seconds=round(delay, 1))
                    time.sleep(delay)
        except li.LinkedInBlocked as exc:
            debug = _dump_debug(page, settings)
            details.append(f"STOPPED: {exc} (debug={debug}) — do not retry today")
            log.error("network_blocked", error=str(exc))
            failed += 1
        finally:
            try:
                context.storage_state(path=str(state_path))
            except Exception:  # noqa: BLE001
                pass
            browser.close()

    details.append(f"sent={sent} skipped={skipped} failed={failed}")
    return StageResult(
        stage="connections-send",
        dry_run=False,
        processed=sent + skipped + failed,
        succeeded=sent,
        skipped=skipped,
        failed=failed,
        details=details,
    )
