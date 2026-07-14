from __future__ import annotations
from app.utils.logger import logger
import os
import time
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from app.config.database import SessionLocal
from app.models.sales_pitch import SalesPitch
from app.models.business_client import Business_Client
from app.models.linkedin_contact import LinkedinContact
from app.services.email_sender import send_pitch_email
from app.scraper.linkedin_dm_sender import send_linkedin_dm
from app.scraper.linkedin_finder import LinkedInSessionExpiredError
from app.models.profile_setting import ProfileSetting


# ── Config ────────────────────────────────────────────────────────────────────

def _cfg() -> dict:
    return {
        "linkedin_dm_limit"   : int(os.getenv("LINKEDIN_DM_DAILY_LIMIT",  "5")),
        "max_attempts"        : int(os.getenv("MAX_DELIVERY_ATTEMPTS",    "3")),
        "delay_between_dms"   : float(os.getenv("LINKEDIN_DM_DELAY_SEC",  "8.0")),
        "delay_between_emails": float(os.getenv("EMAIL_DELIVERY_DELAY_SEC", "2.0")),
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mark_sent(pitch: SalesPitch, db: Session) -> None:
    pitch.delivery_status = "sent"
    pitch.delivered_at    = datetime.now(timezone.utc)
    pitch.delivery_error  = None
    db.commit()


def _mark_failed(pitch: SalesPitch, db: Session, error: str) -> None:
    pitch.delivery_status   = "failed"
    pitch.delivery_error    = error[:2000]   # cap length
    pitch.delivery_attempts = (pitch.delivery_attempts or 0) + 1
    db.commit()


def _mark_skipped(pitch: SalesPitch, db: Session, reason: str) -> None:
    pitch.delivery_status = "skipped"
    pitch.delivery_error  = reason
    db.commit()


def _fetch_pending(db: Session, max_attempts: int) -> list[SalesPitch]:
    """
    Return pitches that are deliverable:
      - delivery_status is 'pending' OR 'failed' (retry eligible)
      - attempts < max_attempts
      - channel is NOT 'generic'
    """
    return (
        db.query(SalesPitch)
        .filter(
            SalesPitch.pitch_channel != "generic",
            SalesPitch.pitch_channel.isnot(None),
            SalesPitch.delivery_status.in_(["pending", "failed"]),
            SalesPitch.delivery_attempts < max_attempts,
        )
        .order_by(SalesPitch.created_at.asc())
        .all()
    )


# ── Core delivery logic ───────────────────────────────────────────────────────

def deliver_pending_pitches(db: Session, profile_id: int = 1) -> dict:
    """
    Fetch all deliverable pitches and attempt to send them.

    Returns a summary dict with counts per outcome.
    """
    cfg = _cfg()
    summary = {
        "email_sent"      : 0,
        "email_failed"    : 0,
        "linkedin_sent"   : 0,
        "linkedin_failed" : 0,
        "skipped"         : 0,
        "linkedin_limit_reached": False,
    }

    profile = db.query(ProfileSetting).filter(ProfileSetting.id == profile_id).first()
    linkedin_dm_limit = profile.max_messages_per_day if profile else cfg["linkedin_dm_limit"]

    pitches = _fetch_pending(db, cfg["max_attempts"])

    if not pitches:
        logger.info("[DELIVERY] No deliverable pitches found.")
        return summary

    logger.info(f"[DELIVERY] 🚀 Starting delivery run — {len(pitches)} pitch(es) to process.")

    linkedin_sent_today = 0

    for pitch in pitches:
        channel = pitch.pitch_channel

        # ── Email channel ──────────────────────────────────────────────────
        if channel == "email":
            client = db.query(Business_Client).filter(
                Business_Client.id == pitch.business_client_id
            ).first()

            if not client or not client.email:
                _mark_skipped(pitch, db, "No email address on business client record")
                summary["skipped"] += 1
                logger.info(f"[DELIVERY] ⏭️  Pitch {pitch.id} skipped — no client email.")
                continue

            try:
                send_pitch_email(
                    to_address=client.email,
                    subject=pitch.pitch_subject or f"Website improvement opportunity for {client.name}",
                    body=pitch.pitch_body or "",
                )
                _mark_sent(pitch, db)
                summary["email_sent"] += 1
                logger.info(f"[DELIVERY] ✅ Email sent → {client.email} (pitch {pitch.id})")

            except Exception as exc:
                _mark_failed(pitch, db, str(exc))
                summary["email_failed"] += 1
                logger.info(f"[DELIVERY] ❌ Email failed for pitch {pitch.id}: {exc}")

            time.sleep(cfg["delay_between_emails"])

        # ── LinkedIn channel ───────────────────────────────────────────────
        elif channel == "linkedin":
            if linkedin_sent_today >= linkedin_dm_limit:
                summary["linkedin_limit_reached"] = True
                logger.info(
                    f"[DELIVERY] 🛑 LinkedIn DM daily limit ({linkedin_dm_limit}) reached. "
                    "Remaining LinkedIn pitches will be sent tomorrow."
                )
                break   # stop processing LinkedIn pitches for today

            if not pitch.linkedin_contact_id:
                _mark_skipped(pitch, db, "No linkedin_contact_id linked to pitch")
                summary["skipped"] += 1
                continue

            contact = db.query(LinkedinContact).filter(
                LinkedinContact.id == pitch.linkedin_contact_id
            ).first()

            if not contact:
                _mark_skipped(pitch, db, "LinkedinContact record not found")
                summary["skipped"] += 1
                continue

            if not contact.is_connected:
                # Not yet connected — leave as pending, pick up after acceptance check
                logger.info(
                    f"[DELIVERY] ⏳ {contact.name} not yet connected — "
                    f"pitch {pitch.id} stays pending."
                )
                continue

            try:
                sent = send_linkedin_dm(contact, pitch.pitch_body or "", db, profile_id=profile_id)
                if sent:
                    _mark_sent(pitch, db)
                    linkedin_sent_today += 1
                    summary["linkedin_sent"] += 1
                    logger.info(
                        f"[DELIVERY] ✅ LinkedIn DM sent → {contact.name} "
                        f"(pitch {pitch.id}, {linkedin_sent_today}/{linkedin_dm_limit} today)"
                    )
                    time.sleep(cfg["delay_between_dms"])
                else:
                    _mark_failed(pitch, db, "DM send returned False (button not found or restricted)")
                    summary["linkedin_failed"] += 1

            except LinkedInSessionExpiredError:
                # Session expired mid-batch — stop all LinkedIn sends
                _mark_failed(pitch, db, "LinkedIn session expired")
                summary["linkedin_failed"] += 1
                logger.info(
                    "[DELIVERY] ⛔ LinkedIn session expired — stopping LinkedIn sends.\n"
                    "  Re-authenticate: python app/scraper/linkdin/save_state.py"
                )
                break

            except Exception as exc:
                _mark_failed(pitch, db, str(exc))
                summary["linkedin_failed"] += 1
                logger.info(f"[DELIVERY] ❌ LinkedIn DM failed for pitch {pitch.id}: {exc}")
                time.sleep(3)   # short cooldown before next attempt

    logger.info(
        f"[DELIVERY] ✅ Done — "
        f"email: {summary['email_sent']} sent / {summary['email_failed']} failed | "
        f"linkedin: {summary['linkedin_sent']} sent / {summary['linkedin_failed']} failed | "
        f"skipped: {summary['skipped']}"
    )
    return summary


# ── Scheduler entry point ─────────────────────────────────────────────────────

def run_pitch_delivery_job(profile_id: int = 1) -> None:
    """Sync entry point called by APScheduler."""
    db = SessionLocal()
    try:
        deliver_pending_pitches(db, profile_id=profile_id)
    except Exception as exc:
        logger.info(f"[DELIVERY] ❌ Unexpected error in delivery job: {exc}")
    finally:
        db.close()
