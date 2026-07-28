import os
import json
import traceback as tb_module
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.executors.pool import ThreadPoolExecutor
from app.config.database import SessionLocal
from app.services.lead_service import run_lead_job, load_config
import threading
from app.services.sheet_exporter import export_to_google_sheet
from app.services.website_audit_service import run_audit_job
from app.services.lead_scoring_service import run_lead_score_job
from app.services.linkedin_service import run_linkedin_batch_job, run_linkedin_daily_connections, run_linkedin_acceptance_check, run_linkedin_search_and_connect
from app.services.pitch_delivery_service import run_pitch_delivery_job
from app.services.pitch_reply_service import run_pitch_reply_check_job
from datetime import datetime, timezone, timedelta
from app.models.profile_setting import ProfileSetting
from app.models.scheduler_job_run import SchedulerJobRun
from app.services.notification_service import notify_job_failure
from app.utils.logger import logger
from app.models.linkedin_search_config import LinkedinSearchConfig

TRACKER_FILE = os.path.join(os.path.dirname(__file__), "last_job_tracker.json")
LOCK_FILE    = os.path.join(os.path.dirname(__file__), "job.lock")  # ADDED: lock file
_scheduler   = None
_job_lock    = threading.Lock()                                      # ADDED: thread lock

def get_last_job() -> dict:
    if os.path.exists(TRACKER_FILE):
        with open(TRACKER_FILE, "r") as f:
            content = f.read().strip()
            if not content:                # ADDED: handle empty file
                return {}
            return json.loads(content)
    return {}

def save_last_job(job: dict):
    with open(TRACKER_FILE, "w") as f:
        json.dump({"category": job.get("category"), "city": job.get("city")}, f)

def find_next_job(jobs: list) -> dict:
    last = get_last_job()
    if not last:
        return jobs[0]
    for i, job in enumerate(jobs):
        if job.get("category") == last.get("category") and \
           job.get("city")     == last.get("city"):
            next_index = (i + 1) % len(jobs)
            return jobs[next_index]
    return jobs[0]

def run_next_job():
    # CHANGED: use threading.Lock to truly block concurrent runs
    if not _job_lock.acquire(blocking=False):
        logger.warning("[SCHEDULER] Job already running, skipping this trigger.")
        return

    try:
        config = load_config()
        jobs   = config.get("jobs", [])

        if not jobs:
            return

        next_job = find_next_job(jobs)
        save_last_job(next_job)

        logger.info(f"[SCHEDULER] Running job: {next_job.get('category')} in {next_job.get('city')}")

        db = SessionLocal()
        try:
            result = run_lead_job(next_job, db)
            logger.info(f"[SCHEDULER] Job completed — scraped: {result.get('scraped')}, saved: {result.get('saved')}, skipped: {result.get('skipped_duplicates')}")
        finally:
            db.close()

    finally:
        _job_lock.release()  ## release so next job can run

def _run_job_for_profiles(job_name: str, func, single_profile_per_run: bool = False):
    db = SessionLocal()
    try:
        profiles = (
            db.query(ProfileSetting)
            .filter(ProfileSetting.is_active == True)
            .order_by(ProfileSetting.last_used_at.asc())
            .all()
        )
        for profile in profiles:
            try:
                allowed = json.loads(profile.allowed_processes or "[]")
            except json.JSONDecodeError:
                allowed = []
            
            if job_name in allowed:
                logger.info(f"[SCHEDULER] Running {job_name} for Profile {profile.id} ({profile.name})")
                func(profile_id=profile.id)
                profile.last_used_at = datetime.now(timezone.utc)
                db.commit()
                if single_profile_per_run:
                    logger.info(f"[SCHEDULER] Single profile mode active for {job_name} — stopping after Profile {profile.id} ({profile.name})")
                    break
            else:
                logger.info(f"[SCHEDULER] Skipping {job_name} for Profile {profile.id} ({profile.name}) - not in allowed processes")
    except Exception as exc:
        logger.error(f"[SCHEDULER] Error running {job_name} for profiles: {exc}")
    finally:
        db.close()

def scheduled_linkedin_batch():
    _run_job_for_profiles("daily_linkedin_search", run_linkedin_search_and_connect, single_profile_per_run=True)

def scheduled_linkedin_connections():
    _run_job_for_profiles("daily_linkedin_connections", run_linkedin_daily_connections)

def scheduled_linkedin_acceptance_check():
    _run_job_for_profiles("daily_linkedin_acceptance_check", run_linkedin_acceptance_check)

def scheduled_pitch_delivery():
    _run_job_for_profiles("daily_pitch_delivery", run_pitch_delivery_job)

def scheduled_pitch_reply_check():
    _run_job_for_profiles("daily_pitch_reply_check", run_pitch_reply_check_job)


# ── Job Tracking Wrapper ──────────────────────────────────────────────────────

def make_tracked_job(
    func,
    job_id: str,
    job_name: str,
    max_retries: int = 2,
    retry_delay_sec: int = 600,
):
    def wrapper(*args, _retry_count: int = 0, **kwargs):
        db  = SessionLocal()
        run = SchedulerJobRun(
            job_id      = job_id,
            job_name    = job_name,
            status      = "running",
            started_at  = datetime.now(timezone.utc),
            retry_count = _retry_count,
        )
        try:
            db.add(run)
            db.commit()
            db.refresh(run)
        except Exception:
            logger.warning(f"[TRACKER] Could not write 'running' record for {job_id}")
        finally:
            db.close()

        run_id = run.id if run.id else None
        start  = datetime.now(timezone.utc)

        try:
            func(*args, **kwargs)

            # ── SUCCESS ───────────────────────────────────────────────────────
            finished = datetime.now(timezone.utc)
            duration = (finished - start).total_seconds()
            _update_run(run_id, "completed", finished, duration, None, None)
            logger.info(f"[TRACKER] ✅ {job_name} completed in {duration:.1f}s")

        except Exception as exc:
            # ── FAILURE ───────────────────────────────────────────────────────
            finished  = datetime.now(timezone.utc)
            duration  = (finished - start).total_seconds()
            err_str   = str(exc)
            tb_str    = tb_module.format_exc()

            _update_run(run_id, "failed", finished, duration, err_str, tb_str)
            logger.error(f"[TRACKER] ❌ {job_name} failed (attempt {_retry_count + 1}): {err_str}")

            notify_job_failure(
                job_id      = job_id,
                job_name    = job_name,
                error       = err_str,
                tb          = tb_str,
                retry_count = _retry_count,
            )

            if _retry_count < max_retries:
                next_retry = _retry_count + 1
                run_at     = datetime.now(timezone.utc) + timedelta(seconds=retry_delay_sec)
                logger.warning(
                    f"[TRACKER] Scheduling retry {next_retry}/{max_retries} for "
                    f"{job_name} at {run_at.strftime('%H:%M:%S UTC')}"
                )
                if _scheduler:
                    _scheduler.add_job(
                        wrapper,
                        trigger   = "date",
                        run_date  = run_at,
                        id        = f"{job_id}_retry_{next_retry}_{int(run_at.timestamp())}",
                        kwargs    = {"_retry_count": next_retry},
                        replace_existing = True,
                    )
            else:
                logger.error(f"[TRACKER] 🚫 {job_name} exhausted all {max_retries} retries.")

    wrapper.__name__ = func.__name__
    return wrapper


def _update_run(
    run_id,
    status: str,
    finished_at: datetime,
    duration_sec: float,
    error_message: str | None,
    traceback: str | None,
) -> None:
    if run_id is None:
        return
    db = SessionLocal()
    try:
        run = db.query(SchedulerJobRun).filter(SchedulerJobRun.id == run_id).first()
        if run:
            run.status        = status
            run.finished_at   = finished_at
            run.duration_sec  = duration_sec
            run.error_message = (error_message or "")[:1000] if error_message else None
            run.traceback     = traceback
            db.commit()
    except Exception as exc:
        logger.warning(f"[TRACKER] Could not update run record {run_id}: {exc}")
    finally:
        db.close()


# ── Job Scheduling ─────────────────────────────────────────────────────────────

def _get_linkedin_search_schedule() -> tuple[int, int]:
    """
    Read the schedule time from the active LinkedinSearchConfig row.
    Falls back to LINKEDIN_SCHEDULE_HOUR / LINKEDIN_SCHEDULE_MINUTE env vars.
    """
    try:
        db = SessionLocal()
        try:
            config = (
                db.query(LinkedinSearchConfig)
                .filter(LinkedinSearchConfig.is_active == True)  # noqa: E712
                .first()
            )
            if config and config.schedule_time:
                time_str = config.schedule_time  # e.g. "14:00" or "14:00:00"
                parts = time_str.split(":")
                hour, minute = int(parts[0]), int(parts[1])
                logger.info(
                    f"[SCHEDULER] LinkedIn schedule from DB: {hour:02d}:{minute:02d}"
                )
                return hour, minute
        finally:
            db.close()
    except Exception as exc:
        logger.warning(f"[SCHEDULER] Could not read LinkedinSearchConfig schedule: {exc}")

    # Fallback to env vars
    return (
        int(os.getenv("LINKEDIN_SCHEDULE_HOUR",   "13")),
        int(os.getenv("LINKEDIN_SCHEDULE_MINUTE",  "45")),
    )


def schedule_jobs():
    global _scheduler
    config    = load_config()
    times     = config.get("schedule_times", ["08:00"])
    executors = {"default": ThreadPoolExecutor(max_workers=3)}
    job_defaults = {
        "coalesce"          : True,
        "max_instances"     : 1,
        "misfire_grace_time": 3600
    }

    _scheduler = BackgroundScheduler(executors=executors, job_defaults=job_defaults)

    linkedin_retry_delay = int(os.getenv("LINKEDIN_JOB_RETRY_DELAY_SEC", "1800"))
    default_retry_delay  = int(os.getenv("DEFAULT_JOB_RETRY_DELAY_SEC",  "600"))

    for time_str in times:
        hour, minute = time_str.split(":")
        # ---- Google Maps Scraper Job ----
        _scheduler.add_job(
            make_tracked_job(run_next_job, job_id=f"scraper_{hour}_{minute}", job_name="Lead Scraper", max_retries=3, retry_delay_sec=default_retry_delay),
            "cron", hour=int(hour), minute=int(minute), id=f"scraper_{hour}_{minute}",
        )

    export_hour_env   = os.getenv("EXPORT_SCHEDULE_HOUR", "9")
    export_minute_env = os.getenv("EXPORT_SCHEDULE_MINUTE", "0")

    if ":" in export_hour_env:
        export_hour, export_minute = map(int, export_hour_env.split(":"))
    else:
        export_hour   = int(export_hour_env)
        export_minute = int(export_minute_env)

    _scheduler.add_job(
        make_tracked_job(export_to_google_sheet, job_id="daily_sheet_export", job_name="Google Sheet Export", max_retries=2, retry_delay_sec=default_retry_delay),
        "cron",
        hour=export_hour,
        minute=export_minute,
        id="daily_sheet_export",
        replace_existing=True,
        jitter=120,
    )

    # ---- Website Audit Job ----
    audit_hour   = int(os.getenv("AUDIT_SCHEDULE_HOUR",   "1"))
    audit_minute = int(os.getenv("AUDIT_SCHEDULE_MINUTE", "0"))
    _scheduler.add_job(
        make_tracked_job(run_audit_job, job_id="daily_website_audit", job_name="Website Audit", max_retries=2, retry_delay_sec=default_retry_delay),
        "cron",
        hour=audit_hour,
        minute=audit_minute,
        id="daily_website_audit",
        replace_existing=True,
        jitter=120,
    )
    logger.info(f"[SCHEDULER] Website audit job scheduled at {audit_hour:02d}:{audit_minute:02d} daily.")

    # ---- LinkedIn Search + Connect Job (driven by LinkedinSearchConfig) ----
    # Schedule time is read from the DB config row; falls back to env vars if not set.
    _linkedin_search_hour, _linkedin_search_minute = _get_linkedin_search_schedule()
    _scheduler.add_job(
        make_tracked_job(scheduled_linkedin_batch, job_id="daily_linkedin_search", job_name="LinkedIn Search + Connect", max_retries=1, retry_delay_sec=linkedin_retry_delay),
        "cron",
        hour=_linkedin_search_hour,
        minute=_linkedin_search_minute,
        id="daily_linkedin_search",
        replace_existing=True,
        jitter=300,
    )
    logger.info(f"[SCHEDULER] LinkedIn search+connect job scheduled at {_linkedin_search_hour:02d}:{_linkedin_search_minute:02d} daily.")

    # ---- Lead Scoring + Pitch Job (Runs after Audit and LinkedIn Search) ----
    score_hour   = int(os.getenv("LEAD_SCORE_SCHEDULE_HOUR",   "3"))
    score_minute = int(os.getenv("LEAD_SCORE_SCHEDULE_MINUTE", "0"))
    _scheduler.add_job(
        make_tracked_job(run_lead_score_job, job_id="daily_lead_scoring", job_name="Lead Scoring", max_retries=2, retry_delay_sec=default_retry_delay),
        "cron",
        hour=score_hour,
        minute=score_minute,
        id="daily_lead_scoring",
        replace_existing=True,
        jitter=120,
    )
    logger.info(f"[SCHEDULER] Lead scoring job scheduled at {score_hour:02d}:{score_minute:02d} daily.")

    # ---- LinkedIn Connections Batch Job (DISABLED — connections now sent inline during search) ----
    # connections_hour   = int(os.getenv("LINKEDIN_CONNECTIONS_SCHEDULE_HOUR",   "4"))
    # connections_minute = int(os.getenv("LINKEDIN_CONNECTIONS_SCHEDULE_MINUTE", "0"))
    # _scheduler.add_job(
    #     make_tracked_job(scheduled_linkedin_connections, job_id="daily_linkedin_connections", job_name="LinkedIn Connections", max_retries=1, retry_delay_sec=linkedin_retry_delay),
    #     "cron",
    #     hour=connections_hour,
    #     minute=connections_minute,
    #     id="daily_linkedin_connections",
    #     replace_existing=True,
    # )
    # logger.info(f"[SCHEDULER] LinkedIn connections job scheduled at {connections_hour:02d}:{connections_minute:02d} daily.")

    # ---- LinkedIn Acceptance Check Job (runs every 2 hours) ----
    acceptance_interval_hours = int(os.getenv("LINKEDIN_ACCEPTANCE_INTERVAL_HOURS", "2"))
    _scheduler.add_job(
        make_tracked_job(scheduled_linkedin_acceptance_check, job_id="linkedin_acceptance_check", job_name="LinkedIn Acceptance Check", max_retries=1, retry_delay_sec=linkedin_retry_delay),
        "interval",
        minutes=acceptance_interval_hours,
        id="linkedin_acceptance_check",
        replace_existing=True,
        jitter=300,
    )
    logger.info(f"[SCHEDULER] LinkedIn acceptance check job scheduled every {acceptance_interval_hours} hour(s).")

    # ---- Pitch Delivery Job (email + LinkedIn DMs, runs after lead scoring) ----
    delivery_hour   = int(os.getenv("PITCH_DELIVERY_SCHEDULE_HOUR",   "14"))
    delivery_minute = int(os.getenv("PITCH_DELIVERY_SCHEDULE_MINUTE", "0"))
    _scheduler.add_job(
        make_tracked_job(scheduled_pitch_delivery, job_id="daily_pitch_delivery", job_name="Pitch Delivery", max_retries=2, retry_delay_sec=default_retry_delay),
        "cron",
        hour=delivery_hour,
        minute=delivery_minute,
        id="daily_pitch_delivery",
        replace_existing=True,
        jitter=120,
    )
    logger.info(f"[SCHEDULER] Pitch delivery job scheduled at {delivery_hour:02d}:{delivery_minute:02d} daily.")

    # ---- LinkedIn Reply Check Job (Hybrid: inbox scan + targeted thread check) ----
    reply_interval_hours = int(os.getenv("REPLY_CHECK_INTERVAL_HOURS", "4"))
    _scheduler.add_job(
        make_tracked_job(scheduled_pitch_reply_check, job_id="periodic_pitch_reply_check", job_name="Pitch Reply Check", max_retries=1, retry_delay_sec=linkedin_retry_delay),
        "interval",
        minutes=reply_interval_hours,
        id="periodic_pitch_reply_check",
        replace_existing=True,
        jitter=45,
    )
    logger.info(f"[SCHEDULER] Pitch reply check job scheduled every {reply_interval_hours} minute(s).")

    _scheduler.start()
    return _scheduler

def get_scheduler():
    return _scheduler

def reload_schedule():
    global _scheduler
    if not _scheduler:
        schedule_jobs()
        return
        
    _scheduler.remove_all_jobs()
    config = load_config()
    times  = config.get("schedule_times", ["08:00"])
    for time_str in times:      
        hour, minute = time_str.split(":")
        _scheduler.add_job(run_next_job, "cron", hour=int(hour), minute=int(minute), id=f"scraper_{hour}_{minute}") 
    logger.info(f"[SCHEDULER] Schedule reloaded with times: {times}")
