"""Typer CLI — `pipeline run` and per-stage commands."""

from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from outreach import compose, enrich, ingest, match, parse, send
from outreach.config import get_settings, reload_settings
from outreach.db.session import init_db
from outreach.logging import configure_logging, get_logger
from outreach.stages import StageResult

app = typer.Typer(
    name="pipeline",
    help="Job-outreach automation pipeline",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()

STAGE_ORDER = ("ingest", "parse", "enrich", "match", "compose", "send")


def _print_result(result: StageResult) -> None:
    console.print(result.line(), markup=False)
    for detail in result.details:
        console.print(f"  - {detail}", markup=False)


def _common_options(
    dry_run: bool,
    verbose: bool,
    config: Optional[str],
) -> None:
    configure_logging(verbose=verbose)
    if config:
        reload_settings(config)
    else:
        get_settings()
    init_db()
    get_logger("cli").info("cli_start", dry_run=dry_run, config=config)


@app.callback()
def main(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging"),
    config: Optional[str] = typer.Option(
        None, "--config", help="Path to config.yaml (default: CONFIG_PATH / config.yaml)"
    ),
) -> None:
    """Job-outreach pipeline CLI."""
    # Store on the context via env-ish globals for subcommands — Typer passes
    # callback params only to the callback; subcommands re-read flags below.
    _ = (verbose, config)


@app.command("ingest")
def cmd_ingest(
    dry_run: bool = typer.Option(False, "--dry-run"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    config: Optional[str] = typer.Option(None, "--config"),
    source: Optional[str] = typer.Option(
        None,
        "--source",
        "-s",
        help="Ingest source: feed | dms | posts | naukri | all (overrides config.yaml)",
    ),
) -> None:
    """Pull raw hiring posts (feed, DMs, Apify, or Naukri discovery)."""
    _common_options(dry_run, verbose, config)
    _print_result(ingest.run(dry_run=dry_run, source=source))


@app.command("run")
def run_all(
    dry_run: bool = typer.Option(False, "--dry-run", help="No API calls or sends"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    config: Optional[str] = typer.Option(None, "--config"),
    skip_enrich: bool = typer.Option(
        False, "--skip-enrich", help="Skip enrich even if enabled in config"
    ),
    source: Optional[str] = typer.Option(
        None,
        "--source",
        "-s",
        help="Ingest source for this run: feed | dms | posts | naukri | all",
    ),
) -> None:
    """Run all stages in order: ingest -> parse -> enrich -> match -> compose -> send."""
    _common_options(dry_run, verbose, config)
    settings = get_settings()

    runners = {
        "ingest": lambda: ingest.run(dry_run=dry_run, source=source),
        "parse": lambda: parse.run(dry_run=dry_run),
        "enrich": lambda: enrich.run(dry_run=dry_run),
        "match": lambda: match.run(dry_run=dry_run),
        "compose": lambda: compose.run(dry_run=dry_run),
        "send": lambda: send.run(dry_run=dry_run),
    }

    for name in STAGE_ORDER:
        if name == "enrich" and (skip_enrich or not settings.config.enrich.enabled):
            _print_result(
                StageResult(
                    stage="enrich",
                    dry_run=dry_run,
                    skipped=1,
                    details=["enrich disabled (config.enrich.enabled=false or --skip-enrich)"],
                )
            )
            continue
        _print_result(runners[name]())


@app.command("parse")
def cmd_parse(
    dry_run: bool = typer.Option(False, "--dry-run"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    config: Optional[str] = typer.Option(None, "--config"),
) -> None:
    """Extract structured fields from ingested posts."""
    _common_options(dry_run, verbose, config)
    _print_result(parse.run(dry_run=dry_run))


@app.command("enrich")
def cmd_enrich(
    dry_run: bool = typer.Option(False, "--dry-run"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    config: Optional[str] = typer.Option(None, "--config"),
) -> None:
    """Find contact emails for posts that need enrichment."""
    _common_options(dry_run, verbose, config)
    _print_result(enrich.run(dry_run=dry_run))


@app.command("match")
def cmd_match(
    dry_run: bool = typer.Option(False, "--dry-run"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    config: Optional[str] = typer.Option(None, "--config"),
) -> None:
    """Score posts against your resume profile."""
    _common_options(dry_run, verbose, config)
    _print_result(match.run(dry_run=dry_run))


@app.command("compose")
def cmd_compose(
    dry_run: bool = typer.Option(False, "--dry-run"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    config: Optional[str] = typer.Option(None, "--config"),
) -> None:
    """Draft personalized application emails."""
    _common_options(dry_run, verbose, config)
    _print_result(compose.run(dry_run=dry_run))


@app.command("send")
def cmd_send(
    dry_run: bool = typer.Option(False, "--dry-run"),
    force: bool = typer.Option(
        False,
        "--force",
        help="Bypass business-hours check (for testing only)",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    config: Optional[str] = typer.Option(None, "--config"),
) -> None:
    """Send approved drafts via Gmail (throttled)."""
    _common_options(dry_run, verbose, config)
    _print_result(send.run(dry_run=dry_run, force=force))


@app.command("review")
def cmd_review(
    dry_run: bool = typer.Option(False, "--dry-run"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    config: Optional[str] = typer.Option(None, "--config"),
) -> None:
    """Approve / edit / reject pending email drafts."""
    _common_options(dry_run, verbose, config)
    _print_result(send.review(dry_run=dry_run))


@app.command("status")
def cmd_status(
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    config: Optional[str] = typer.Option(None, "--config"),
) -> None:
    """Show post counts by status in SQLite."""
    _common_options(False, verbose, config)
    from sqlmodel import func, select

    from outreach.db.models import Post
    from outreach.db.session import session_scope

    with session_scope() as session:
        rows = session.exec(
            select(Post.status, func.count()).group_by(Post.status)
        ).all()

    table = Table(title="Posts by status")
    table.add_column("Status")
    table.add_column("Count", justify="right")
    if not rows:
        table.add_row("(empty)", "0")
    else:
        for status, count in rows:
            table.add_row(str(status.value if hasattr(status, "value") else status), str(count))
    console.print(table)


@app.command("linkedin-login")
def cmd_linkedin_login(
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    config: Optional[str] = typer.Option(None, "--config"),
    timeout: int = typer.Option(300, "--timeout", help="Seconds to wait for manual login"),
) -> None:
    """Open a browser, log into LinkedIn once, save session for feed/DM ingest."""
    _common_options(False, verbose, config)
    from outreach.ingest.sources.linkedin_feed import ensure_logged_in_session

    settings = get_settings()
    path = ensure_logged_in_session(settings, timeout_seconds=timeout)
    console.print(f"Saved LinkedIn session to {path}", markup=False)
    console.print(
        "Then: uv run pipeline ingest -s feed   (or -s dms)",
        markup=False,
    )


@app.command("naukri-login")
def cmd_naukri_login(
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    config: Optional[str] = typer.Option(None, "--config"),
    timeout: int = typer.Option(300, "--timeout", help="Seconds to wait for manual login"),
) -> None:
    """Optional Naukri login — saves session for job discovery (never auto-applies)."""
    _common_options(False, verbose, config)
    from outreach.ingest.sources.naukri import ensure_naukri_session

    settings = get_settings()
    path = ensure_naukri_session(settings, timeout_seconds=timeout)
    console.print(f"Saved Naukri session to {path}", markup=False)
    console.print(
        "Then: uv run pipeline ingest -s naukri",
        markup=False,
    )


@app.command("shortlist")
def cmd_shortlist(
    dry_run: bool = typer.Option(False, "--dry-run"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    config: Optional[str] = typer.Option(None, "--config"),
    source: Optional[str] = typer.Option(
        "naukri",
        "--source",
        "-s",
        help="Filter by source (default naukri). Use 'all' for every source.",
    ),
    min_score: Optional[int] = typer.Option(
        None, "--min-score", help="Override match cutoff for the shortlist"
    ),
    limit: int = typer.Option(50, "--limit", help="Max jobs in the shortlist file"),
) -> None:
    """Write a markdown alert list of matching jobs for MANUAL apply."""
    from outreach import shortlist as shortlist_mod

    _common_options(dry_run, verbose, config)
    src = None if source and source.lower().strip() == "all" else source
    result = shortlist_mod.run(
        source=src,
        min_score=min_score,
        limit=limit,
        dry_run=dry_run,
    )
    _print_result(result)
    if not dry_run and result.details:
        for d in result.details:
            if d.startswith("path="):
                console.print(f"Open {d.removeprefix('path=')}", markup=False)


@app.command("init-db")
def cmd_init_db(
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    config: Optional[str] = typer.Option(None, "--config"),
) -> None:
    """Create SQLite tables (also runs automatically on other commands)."""
    _common_options(False, verbose, config)
    settings = get_settings()
    console.print(f"Database ready at {settings.database_url}")


if __name__ == "__main__":
    app()
