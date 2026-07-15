from app.utils.logger import logger
from app.services.notification_service import notify_connection_accepted, notify_reply_received
import threading
import time
import os
from typing import cast
from sqlalchemy.orm import Session
from app.config.database import SessionLocal
from app.models.business_client import Business_Client
from app.scraper.linkedin_finder import (
    find_linkedin_playwright,
    LinkedInSessionExpiredError,
    BETWEEN_COMPANIES_DELAY,
)
from app.scraper.linkedin_connector import send_connection_requests, send_daily_global_connections, check_recent_connections
from app.scraper.linkedin_search_connector import run_linkedin_search_and_send_connections
from app.models.linkedin_contact import LinkedinContact
from app.models.business_client import Business_Client
from app.models.linkedin_search_config import LinkedinSearchConfig

MAX_CONCURRENT_PROFILES = int(os.getenv("MAX_CONCURRENT_PROFILES", "1"))
_linkedin_semaphore = threading.BoundedSemaphore(MAX_CONCURRENT_PROFILES)
_profile_locks = {}
_profile_locks_lock = threading.Lock()

def get_profile_lock(profile_id: int) -> threading.Lock:
    with _profile_locks_lock:
        if profile_id not in _profile_locks:
            _profile_locks[profile_id] = threading.Lock()
        return _profile_locks[profile_id]

## On-demand: search one client
def run_linkedin_enrichment(business_client_id: int, db: Session, profile_id: int = 1) -> dict:
    profile_lock = get_profile_lock(profile_id)
    
    if not profile_lock.acquire(blocking=False):
        logger.info(f"[LINKEDIN SERVICE] Profile {profile_id} is currently running another task — skipped.")
        return {"error": "profile_busy"}

    if not _linkedin_semaphore.acquire(blocking=False):
        logger.info("[LINKEDIN SERVICE] Max concurrent LinkedIn sessions reached — skipped.")
        profile_lock.release()
        return {"error": "max_concurrent_sessions"}

    try:
        client = db.query(Business_Client).filter(Business_Client.id == business_client_id).first()
        if not client:
            return {"error": "client_not_found"}

        if client.is_linkedin_searched:
            return {"skipped": True, "status": client.linkedin_search_status}

        logger.info(f"[LINKEDIN SERVICE] Starting enrichment for: {client.name} (id={business_client_id})")
        summary = find_linkedin_playwright(str(client.name), business_client_id, db)
        logger.info(f"[LINKEDIN SERVICE] Enrichment result: {summary}")
        return summary

    except LinkedInSessionExpiredError as e:
        logger.info(f"[LINKEDIN SERVICE] Session expired: {e}")
        return {"error": "session_expired"}

    except Exception as exc:
        logger.info(f"[LINKEDIN SERVICE] Unexpected error: {exc}")
        return {"error": str(exc)}

    finally:
        _linkedin_semaphore.release()
        profile_lock.release()



## On-demand: send connections for one client
def run_linkedin_connections(
    business_client_id: int,
    db: Session,
    limit: int = 5,
    message: str | None = None,
    profile_id: int = 1,
) -> dict:
    """
    Send LinkedIn connection requests for a single business client.
    Called by the FastAPI BackgroundTasks handler.
    """
    profile_lock = get_profile_lock(profile_id)
    if not profile_lock.acquire(blocking=False):
        logger.info(f"[LINKEDIN SERVICE] Profile {profile_id} is busy — skipped.")
        return {"error": "profile_busy"}

    if not _linkedin_semaphore.acquire(blocking=False):
        logger.info("[LINKEDIN SERVICE] Max concurrent LinkedIn sessions reached — skipped.")
        profile_lock.release()
        return {"error": "max_concurrent_sessions"}

    try:
        client = db.query(Business_Client).filter(Business_Client.id == business_client_id).first()
        if not client:
            return {"error": "client_not_found"}

        logger.info(f"[LINKEDIN SERVICE] Sending connections for: {client.name} (id={business_client_id}), limit={limit}")
        result = send_connection_requests(business_client_id, db, limit=limit, message=message)
        logger.info(f"[LINKEDIN SERVICE] Connection result: {result}")
        return result

    except LinkedInSessionExpiredError as e:
        logger.info(f"[LINKEDIN SERVICE] Session expired: {e}")
        return {"error": "session_expired", "session_expired": True}

    except Exception as exc:
        logger.info(f"[LINKEDIN SERVICE] Unexpected error: {exc}")
        return {"error": str(exc)}

    finally:
        _linkedin_semaphore.release()
        profile_lock.release()


## Nightly batch: called by APScheduler
def run_linkedin_batch_job(profile_id: int = 1) -> None:
    profile_lock = get_profile_lock(profile_id)
    if not profile_lock.acquire(blocking=False):
        logger.info(f"[LINKEDIN BATCH] Profile {profile_id} is busy — batch skipped.")
        return

    if not _linkedin_semaphore.acquire(blocking=False):
        logger.info("[LINKEDIN BATCH] Max concurrent sessions — batch skipped.")
        profile_lock.release()
        return

    db = SessionLocal()
    try:
        pending_clients = (
            db.query(Business_Client)
            .filter(Business_Client.is_linkedin_searched == False)
            .order_by(Business_Client.created_at.asc())
            .limit(5)
            .all()
        )

        if not pending_clients:
            logger.info("[LINKEDIN BATCH] No pending clients to search.")
            return

        logger.info(f"[LINKEDIN BATCH] 🚀 Starting batch — {len(pending_clients)} client(s) to search")

        for client in pending_clients:
            logger.info(f"\n[LINKEDIN BATCH] ── Processing: {client.name} (id={client.id})")
            try:
                summary = find_linkedin_playwright(str(client.name), cast(int, client.id), db)
                logger.info(f"[LINKEDIN BATCH] Result: {summary}")

            except LinkedInSessionExpiredError:
                logger.info(
                    "\n[LINKEDIN BATCH] ⛔ Session expired — stopping batch.\n"
                    "  Re-run:  python app/scraper/linkdin/save_state.py\n"
                    "  Remaining clients will be picked up in the next run.\n"
                )
                break   # stop processing remaining clients

            except Exception as exc:
                logger.info(f"[LINKEDIN BATCH] ❌ Error for '{client.name}': {exc} — continuing to next client")
                continue

            # Pause between companies to avoid LinkedIn bot detection
            logger.info(f"[LINKEDIN BATCH] ⏳ Waiting {BETWEEN_COMPANIES_DELAY}s before next company...")
            time.sleep(BETWEEN_COMPANIES_DELAY)

        logger.info("[LINKEDIN BATCH] ✅ Batch complete")

    finally:
        db.close()
        _linkedin_semaphore.release()
        profile_lock.release()

## Nightly batch: Connections
def run_linkedin_daily_connections(profile_id: int = 1) -> None:
    profile_lock = get_profile_lock(profile_id)
    if not profile_lock.acquire(blocking=False):
        logger.info(f"[LINKEDIN CONNECTOR BATCH] Profile {profile_id} is busy — batch skipped.")
        return

    if not _linkedin_semaphore.acquire(blocking=False):
        logger.info("[LINKEDIN CONNECTOR BATCH] Max concurrent sessions — batch skipped.")
        profile_lock.release()
        return

    db = SessionLocal()
    try:
        from app.models.profile_setting import ProfileSetting
        profile = db.query(ProfileSetting).filter(ProfileSetting.id == profile_id).first()
        limit = profile.max_connections_per_day if profile else 5
        logger.info(f"[LINKEDIN CONNECTOR BATCH] Starting daily connections job for Profile {profile_id} (limit={limit}).")
        
        send_daily_global_connections(db, limit=limit, profile_id=profile_id)
        
        logger.info("[LINKEDIN CONNECTOR BATCH] Finished daily connections job.")
    except Exception as exc:
        logger.info(f"[LINKEDIN CONNECTOR BATCH] Unexpected error: {exc}")
    finally:
        db.close()
        _linkedin_semaphore.release()
        profile_lock.release()
## Nightly batch: Check which connections have been accepted
def run_linkedin_acceptance_check(profile_id: int = 1) -> None:
    profile_lock = get_profile_lock(profile_id)
    if not profile_lock.acquire(blocking=False):
        logger.info(f"[LINKEDIN ACCEPTANCE] Profile {profile_id} is busy — skipped.")
        return

    if not _linkedin_semaphore.acquire(blocking=False):
        logger.info("[LINKEDIN ACCEPTANCE] Max concurrent sessions — skipped.")
        profile_lock.release()
        return

    db = SessionLocal()
    try:
        logger.info("[LINKEDIN ACCEPTANCE] Starting daily acceptance check.")
        result = check_recent_connections(db)
        logger.info(
            f"[LINKEDIN ACCEPTANCE] Done — checked={result['checked']}, "
            f"newly_accepted={result['newly_accepted']}, names={result['names']}"
        )
        # Notify for each newly accepted connection
        for name in result.get("names", []):
            # Look up the contact record so we can include profile_url in the alert
            contact_record = (
                db.query(LinkedinContact)
                .filter(LinkedinContact.name == name, LinkedinContact.is_connected == True)  # noqa: E712
                .order_by(LinkedinContact.connected_at.desc())
                .first()
            )
            company_name = ""
            profile_url  = contact_record.profile_url if contact_record else ""
            if contact_record:
                client = db.query(Business_Client).filter(
                    Business_Client.id == contact_record.business_client_id
                ).first()
                company_name = client.name if client else ""
            notify_connection_accepted(
                contact_name = name,
                profile_url  = profile_url,
                company_name = company_name,
            )
    except Exception as exc:
        logger.info(f"[LINKEDIN ACCEPTANCE] Unexpected error: {exc}")
    finally:
        db.close()
        _linkedin_semaphore.release()
        profile_lock.release()


## Scheduled batch: Search LinkedIn by position+location and send connections inline
def run_linkedin_search_and_connect(profile_id: int = 1) -> None:
    """
    Scheduled daily job (called by APScheduler via _run_job_for_profiles).

    Flow:
      1. Load the active LinkedinSearchConfig (positions list + location).
      2. For each position — search LinkedIn, click Show all, send up to 19 connections.
      3. Navigate to My Network > People you may know > Show all — send up to 20 connections.
      4. Persist every sent connection to linkedin_search_contacts table.
    """
    profile_lock = get_profile_lock(profile_id)
    if not profile_lock.acquire(blocking=False):
        logger.info(f"[LINKEDIN SEARCH JOB] Profile {profile_id} is busy — skipped.")
        return

    if not _linkedin_semaphore.acquire(blocking=False):
        logger.info("[LINKEDIN SEARCH JOB] Max concurrent sessions — skipped.")
        profile_lock.release()
        return

    db = SessionLocal()
    try:
        # Load the active search config
        config = (
            db.query(LinkedinSearchConfig)
            .filter(LinkedinSearchConfig.is_active == True)  # noqa: E712
            .first()
        )

        if not config:
            logger.info("[LINKEDIN SEARCH JOB] No active LinkedinSearchConfig found — skipping.")
            return

        positions = config.positions or []
        location  = config.location  or "Bahrain"

        if not positions:
            logger.info("[LINKEDIN SEARCH JOB] No positions configured — skipping.")
            return

        logger.info(
            f"[LINKEDIN SEARCH JOB] 🚀 Starting for Profile {profile_id} | "
            f"Positions: {positions} | Location: {location}"
        )

        result = run_linkedin_search_and_send_connections(
            positions  = positions,
            location   = location,
            profile_id = profile_id,
            db         = db,
        )

        logger.info(
            f"[LINKEDIN SEARCH JOB] ✅ Done — "
            f"search_sent={result['search_connections_sent']}, "
            f"network_sent={result['network_connections_sent']}, "
            f"session_expired={result['session_expired']}"
        )

        if result.get("session_expired"):
            logger.warning(
                "[LINKEDIN SEARCH JOB] ⚠️  Session expired. "
                "Re-run save_state.py to refresh the session."
            )

    except Exception as exc:
        logger.error(f"[LINKEDIN SEARCH JOB] Unexpected error: {exc}")
    finally:
        db.close()
        _linkedin_semaphore.release()
        profile_lock.release()
