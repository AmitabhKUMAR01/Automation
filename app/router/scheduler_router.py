from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config.database import get_db
from app.models.scheduler_job_run import SchedulerJobRun
from app.utils.response import error, success


router = APIRouter(prefix="/scheduler", tags=["Scheduler"])

# Only these outreach-relevant job IDs are surfaced in the dashboard
# Values must exactly match the `job_id` column in scheduler_job_runs
OUTREACH_JOB_IDS = [
    "daily_linkedin_search",
    "daily_linkedin_connections",
    "daily_linkedin_acceptance_check",
    "linkedin_acceptance_check",       # legacy variant
    "daily_pitch_delivery",
    "daily_pitch_reply_check",
    "periodic_pitch_reply_check",      # periodic variant
    "periodic_followup_reply_check",
]

# Map DB status values to frontend-expected values
_STATUS_MAP = {
    "completed": "success",
    "failed": "failed",
    "running": "running",
}


def _normalise_status(status: str) -> str:
    return _STATUS_MAP.get(status, status)


def _timeframe_start(timeframe: str) -> Optional[datetime]:
    if timeframe == "7d":
        return datetime.now() - timedelta(days=7)
    if timeframe == "30d":
        return datetime.now() - timedelta(days=30)
    if timeframe == "all":
        return None
    raise ValueError("Invalid timeframe. Use 7d, 30d, or all.")


# When filtering by a canonical job_id, also include its legacy/periodic aliases
JOB_TYPE_ALIASES: dict[str, list[str]] = {
    "daily_linkedin_acceptance_check": [
        "daily_linkedin_acceptance_check",
        "linkedin_acceptance_check",
    ],
    "daily_pitch_reply_check": [
        "daily_pitch_reply_check",
        "periodic_pitch_reply_check",
    ],
}


def _base_filters(timeframe: str, job_status: str, job_type: str):
    """Return a list of SQLAlchemy filter expressions for the shared query scope."""
    filters = [SchedulerJobRun.job_id.in_(OUTREACH_JOB_IDS)]

    start_at = _timeframe_start(timeframe)
    if start_at:
        filters.append(SchedulerJobRun.started_at >= start_at)

    if job_status != "all":
        # frontend sends "success" but DB stores "completed"
        db_status = "completed" if job_status == "success" else job_status
        filters.append(SchedulerJobRun.status == db_status)

    if job_type != "all" and job_type in OUTREACH_JOB_IDS:
        # expand to include any legacy/periodic aliases for this job type
        ids_to_match = JOB_TYPE_ALIASES.get(job_type, [job_type])
        filters.append(SchedulerJobRun.job_id.in_(ids_to_match))

    return filters


def _build_health(db: Session, timeframe: str, job_status: str, job_type: str) -> dict:
    """Compute summary tiles and per-job-type chart data."""
    filters = _base_filters(timeframe, job_status, job_type)

    # ── aggregate totals ───────────────────────────────────────────────────────
    rows = (
        db.query(
            SchedulerJobRun.status,
            func.count(SchedulerJobRun.id).label("cnt"),
            func.avg(SchedulerJobRun.duration_sec).label("avg_dur"),
            func.sum(SchedulerJobRun.retry_count).label("retries"),
        )
        .filter(*filters)
        .group_by(SchedulerJobRun.status)
        .all()
    )

    total_success = 0
    total_failed = 0
    total_retries = 0
    weighted_dur_sum = 0.0
    weighted_dur_count = 0

    for r in rows:
        norm = _normalise_status(r.status)
        cnt = int(r.cnt or 0)
        if norm == "success":
            total_success = cnt
        elif norm == "failed":
            total_failed = cnt
        total_retries += int(r.retries or 0)
        if r.avg_dur is not None:
            weighted_dur_sum += float(r.avg_dur) * cnt
            weighted_dur_count += cnt

    avg_duration = (
        round(weighted_dur_sum / weighted_dur_count, 2) if weighted_dur_count else 0.0
    )

    # ── per-job-type breakdown ─────────────────────────────────────────────────
    by_type_rows = (
        db.query(
            SchedulerJobRun.job_id,
            SchedulerJobRun.status,
            func.count(SchedulerJobRun.id).label("cnt"),
            func.avg(SchedulerJobRun.duration_sec).label("avg_dur"),
            func.sum(SchedulerJobRun.retry_count).label("retries"),
        )
        .filter(*filters)
        .group_by(SchedulerJobRun.job_id, SchedulerJobRun.status)
        .all()
    )

    job_buckets: dict[str, dict] = {}
    for r in by_type_rows:
        jid = r.job_id
        if jid not in job_buckets:
            job_buckets[jid] = {"success": 0, "failed": 0, "avgDurationSec": 0.0, "retries": 0, "_dur_sum": 0.0, "_dur_n": 0}
        norm = _normalise_status(r.status)
        cnt = int(r.cnt or 0)
        if norm == "success":
            job_buckets[jid]["success"] += cnt
        elif norm == "failed":
            job_buckets[jid]["failed"] += cnt
        job_buckets[jid]["retries"] += int(r.retries or 0)
        if r.avg_dur is not None:
            job_buckets[jid]["_dur_sum"] += float(r.avg_dur) * cnt
            job_buckets[jid]["_dur_n"] += cnt

    by_job_type = []
    for jid in OUTREACH_JOB_IDS:  # preserve consistent order
        if jid not in job_buckets:
            continue
        b = job_buckets[jid]
        avg_dur = round(b["_dur_sum"] / b["_dur_n"], 2) if b["_dur_n"] else 0.0
        by_job_type.append({
            "jobType": jid,
            "success": b["success"],
            "failed": b["failed"],
            "avgDurationSec": avg_dur,
            "retries": b["retries"],
        })

    return {
        "totalSuccess": total_success,
        "totalFailed": total_failed,
        "avgDurationSec": avg_duration,
        "totalRetries": total_retries,
        "byJobType": by_job_type,
    }


def _build_runs_page(
    db: Session,
    timeframe: str,
    job_status: str,
    job_type: str,
    page: int,
    page_size: int,
) -> dict:
    """Return a paginated, normalised list of recent job runs (latest first)."""
    filters = _base_filters(timeframe, job_status, job_type)

    total = (
        db.query(func.count(SchedulerJobRun.id)).filter(*filters).scalar() or 0
    )
    total_pages = max(1, -(-total // page_size))  # ceiling division
    offset = (page - 1) * page_size

    runs = (
        db.query(SchedulerJobRun)
        .filter(*filters)
        .order_by(SchedulerJobRun.started_at.desc())
        .offset(offset)
        .limit(page_size)
        .all()
    )

    items = [
        {
            "id": str(r.id),
            "jobType": r.job_id,
            "profileName": r.job_name,
            "status": _normalise_status(r.status),
            "startedAt": r.started_at.isoformat() if r.started_at else None,
            "durationSec": round(r.duration_sec, 2) if r.duration_sec is not None else 0.0,
            "retries": r.retry_count,
            "message": r.error_message or "",
        }
        for r in runs
    ]

    return {
        "items": items,
        "page": page,
        "pageSize": page_size,
        "total": int(total),
        "totalPages": total_pages,
    }


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("/health")
def get_scheduler_health(
    timeframe: str = Query("30d"),
    job_status: str = Query("all", alias="jobStatus"),
    job_type: str = Query("all", alias="jobType"),
    db: Session = Depends(get_db),
):
    try:
        health = _build_health(db, timeframe, job_status, job_type)
        return success(health, "Scheduler health fetched successfully")
    except ValueError as exc:
        return error(str(exc), status_code=400)
    except Exception as exc:
        return error(f"Failed to fetch scheduler health: {exc}")


@router.get("/runs")
def get_scheduler_runs(
    timeframe: str = Query("30d"),
    job_status: str = Query("all", alias="jobStatus"),
    job_type: str = Query("all", alias="jobType"),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100, alias="pageSize"),
    db: Session = Depends(get_db),
):
    try:
        data = _build_runs_page(db, timeframe, job_status, job_type, page, page_size)
        return success(data, "Scheduler runs fetched successfully")
    except ValueError as exc:
        return error(str(exc), status_code=400)
    except Exception as exc:
        return error(f"Failed to fetch scheduler runs: {exc}")
