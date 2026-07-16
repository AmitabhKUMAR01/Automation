from __future__ import annotations
from app.utils.logger import logger
import os
import time
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session

from app.config.database import SessionLocal
from app.models.sales_pitch import SalesPitch
from app.models.linkedin_contact import LinkedinContact
from app.models.linkedin_search_contact import LinkedinSearchContact
import app.models.lead_score        # noqa: F401  — registers lead_scores table
import app.models.business_client   # noqa: F401  — registers business_clients table
import app.models.linkedin_contact  # noqa: F401  — registers linkedin_contacts table
import app.models.linkedin_search_contact  # noqa: F401  — registers linkedin_search_contacts table
from app.scraper.linkedin_reply_checker import check_reply_for_contact
from app.scraper.linkedin_finder import LinkedInSessionExpiredError
from app.services.notification_service import notify_reply_received
from app.models.business_client import Business_Client

def _cfg() -> dict:
    return {
        # Only re-check pitches whose reply_checked_at is older than this many hours
        "recheck_hours"    : int(os.getenv("REPLY_RECHECK_HOURS",      "12")),
        # Maximum pitches to check per run (avoid long Playwright sessions)
        "max_per_run"      : int(os.getenv("REPLY_MAX_PER_RUN",        "20")),
        # Delay between each contact check (seconds) — reduces LinkedIn bot risk
        "delay_between"    : float(os.getenv("REPLY_CHECK_DELAY_SEC",  "10.0")),
    }


# ── DB Helpers ─────────────────────────────────────────────────────────────────

def _mark_replied(pitch: SalesPitch, reply_text: str, db: Session) -> None:
    now = datetime.now(timezone.utc)
    pitch.reply_received    = True
    pitch.reply_text        = reply_text[:2000]
    pitch.replied_at        = now
    pitch.reply_checked_at  = now
    db.commit()


def _mark_checked_no_reply(pitch: SalesPitch, db: Session) -> None:
    pitch.reply_checked_at = datetime.now(timezone.utc)
    db.commit()


def _fetch_checkable_pitches(db: Session, recheck_hours: int, max_per_run: int) -> list[SalesPitch]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=recheck_hours)

    return (
        db.query(SalesPitch)
        .filter(
            SalesPitch.pitch_channel    == "linkedin",
            SalesPitch.delivery_status  == "sent",
            SalesPitch.reply_received   == False,          # noqa: E712
            (
                SalesPitch.reply_checked_at.is_(None)
                | (SalesPitch.reply_checked_at < cutoff)
            ),
        )
        .order_by(SalesPitch.delivered_at.asc())
        .limit(max_per_run)
        .all()
    )


# ── Core logic ─────────────────────────────────────────────────────────────────

def check_pitch_replies(db: Session, profile_id: int = 1) -> dict:
    cfg = _cfg()
    summary = {
        "checked"      : 0,
        "replied"      : 0,
        "no_reply"     : 0,
        "skipped"      : 0,
        "session_error": False,
    }

    pitches = _fetch_checkable_pitches(db, cfg["recheck_hours"], cfg["max_per_run"])

    if not pitches:
        logger.info("[REPLY CHECK] ℹ️   No pitches eligible for reply check.")
        return summary

    logger.info(f"[REPLY CHECK] 🔍 Starting reply check — {len(pitches)} pitch(es) to process.")

    for pitch in pitches:
        summary["checked"] += 1

        # ── Resolve the linked contact ─────────────────────────────────────────
        if not pitch.linkedin_contact_id and not pitch.linkedin_search_contact_id:
            logger.info(f"[REPLY CHECK] ⏭️  Pitch {pitch.id} has no linked contact — skipping.")
            summary["skipped"] += 1
            _mark_checked_no_reply(pitch, db)
            continue

        if pitch.linkedin_search_contact_id:
            contact = db.query(LinkedinSearchContact).filter(
                LinkedinSearchContact.id == pitch.linkedin_search_contact_id
            ).first()
            contact_type = "LinkedinSearchContact"
        else:
            contact = db.query(LinkedinContact).filter(
                LinkedinContact.id == pitch.linkedin_contact_id
            ).first()
            contact_type = "LinkedinContact"

        if not contact or not contact.profile_url:
            logger.info(f"[REPLY CHECK] ⏭️  Pitch {pitch.id} — {contact_type} not found or no profile_url.")
            summary["skipped"] += 1
            _mark_checked_no_reply(pitch, db)
            continue

        # ── Call the Playwright scraper ────────────────────────────────────────
        try:
            result = check_reply_for_contact(
                profile_url  = contact.profile_url,
                contact_name = contact.name or "Unknown",
                db           = db,
                delivered_at = pitch.delivered_at,
                profile_id   = profile_id,
            )

            if result["replied"]:
                _mark_replied(pitch, result["reply_text"] or "", db)
                summary["replied"] += 1
                logger.info(
                    f"[REPLY CHECK] 🎉 Reply recorded for pitch {pitch.id} "
                    f"({contact.name}): {(result['reply_text'] or '')[:60]!r}"
                )
                company_name = ""
                if pitch.business_client_id:
                    client = db.query(Business_Client).filter(
                        Business_Client.id == pitch.business_client_id
                    ).first()
                    company_name = client.name if client else ""
                notify_reply_received(
                    contact_name  = contact.name or "Unknown",
                    company_name  = company_name,
                    reply_snippet = result["reply_text"] or "",
                )
            else:
                _mark_checked_no_reply(pitch, db)
                summary["no_reply"] += 1
                logger.info(f"[REPLY CHECK] ℹ️   No reply yet — pitch {pitch.id} ({contact.name})")

        except LinkedInSessionExpiredError:
            summary["session_error"] = True
            logger.info(
                "[REPLY CHECK] ⛔ LinkedIn session expired — stopping reply check.\n"
                "  Re-authenticate: python app/scraper/linkdin/save_state.py"
            )
            break

        except Exception as exc:
            logger.info(f"[REPLY CHECK] ❌ Unexpected error for pitch {pitch.id}: {exc}")
            _mark_checked_no_reply(pitch, db)
            summary["no_reply"] += 1

        # Polite delay between Playwright sessions
        time.sleep(cfg["delay_between"])

    logger.info(
        f"[REPLY CHECK] ✅ Done — "
        f"checked: {summary['checked']} | "
        f"replied: {summary['replied']} | "
        f"no_reply: {summary['no_reply']} | "
        f"skipped: {summary['skipped']}"
    )
    return summary


# ── APScheduler entry point ────────────────────────────────────────────────────

def run_pitch_reply_check_job(profile_id: int = 1) -> None:
    """Sync entry point called by APScheduler."""
    db = SessionLocal()
    try:
        check_pitch_replies(db, profile_id=profile_id)
    except Exception as exc:
        logger.info(f"[REPLY CHECK] ❌ Unexpected error in reply check job: {exc}")
    finally:
        db.close()
