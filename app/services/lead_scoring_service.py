from __future__ import annotations
from app.utils.logger import logger
import uuid as _uuid
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import and_
from app.models.business_client import Business_Client
from app.models.lead_score import LeadScore
from app.models.sales_pitch import SalesPitch
from app.models.website_audit import WebsiteAudit
from app.models.linkedin_contact import LinkedinContact
from app.services.sales_pitch_service import generate_sales_pitch, rank_contacts


## Helper Functions

def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _grade(score: float) -> str:
    if score >= 80:
        return "A"
    if score >= 65:
        return "B"
    if score >= 50:
        return "C"
    if score >= 35:
        return "D"
    return "F"

## Core Scoring Engine
def compute_lead_score(audit: WebsiteAudit) -> dict:
    issues: list[str] = []
    breakdown: dict[str, float] = {}

    ## Performance (25 pts)
    perf = 25.0
    load = audit.load_time_ms
    if load is None:
        # dom_content_loaded fallback
        load = audit.dom_content_loaded_ms

    if load is None:
        perf -= 10
        issues.append("Page load time could not be measured")
    elif load > 8000:
        perf -= 20
        issues.append(f"Very slow load time ({int(load)}ms — target <3s)")
    elif load > 5000:
        perf -= 12
        issues.append(f"Slow load time ({int(load)}ms — target <3s)")
    elif load > 3000:
        perf -= 6
        issues.append(f"Above-average load time ({int(load)}ms)")

    fcp = audit.first_paint_ms
    if fcp and fcp > 3000:
        perf -= 5
        issues.append(f"High First Paint time ({int(fcp)}ms — target <1.8s)")
    elif fcp and fcp > 1800:
        perf -= 2

    breakdown["performance"] = round(_clamp(perf, 0, 25), 1)

    # ── 2. SEO (25 pts) ─────────────────────────────────────────────────────
    seo = 25.0
    if not audit.seo_title:
        seo -= 5
        issues.append("Missing SEO title tag")
    if not audit.seo_description:
        seo -= 5
        issues.append("Missing meta description")
    if not audit.has_h1:
        seo -= 4
        issues.append("No H1 heading found on page")
    if not audit.has_canonical:
        seo -= 3
        issues.append("Missing canonical URL tag")
    if not audit.og_title:
        seo -= 3
        issues.append("Missing Open Graph title (og:title)")
    if not audit.og_description:
        seo -= 3
        issues.append("Missing Open Graph description (og:description)")
    if not audit.og_image:
        seo -= 2
        issues.append("Missing Open Graph image (og:image)")

    breakdown["seo"] = round(_clamp(seo, 0, 25), 1)

    # ── 3. Accessibility (20 pts) ───────────────────────────────────────────
    a11y = 20.0
    missing_alt = audit.missing_alt_images_count or 0
    if missing_alt > 0:
        deduction = min(8, missing_alt * 1.5)
        a11y -= deduction
        issues.append(f"{missing_alt} image(s) missing alt text (accessibility & SEO risk)")

    missing_labels = audit.missing_label_inputs_count or 0
    if missing_labels > 0:
        deduction = min(6, missing_labels * 1.5)
        a11y -= deduction
        issues.append(f"{missing_labels} form input(s) missing labels (accessibility issue)")

    if not audit.has_lang_attr:
        a11y -= 4
        issues.append("HTML element missing lang attribute")

    breakdown["accessibility"] = round(_clamp(a11y, 0, 20), 1)

    # ── 4. Security / HTTP (15 pts) ─────────────────────────────────────────
    sec = 15.0
    if not audit.is_https:
        sec -= 10
        issues.append("Site not served over HTTPS — major security risk")

    http_code: Optional[int] = audit.http_status_code
    if http_code is None:
        sec -= 5
        issues.append("HTTP status code could not be determined")
    elif http_code >= 500:
        sec -= 12
        issues.append(f"Server error detected (HTTP {http_code})")
    elif http_code >= 400:
        sec -= 10
        issues.append(f"Client error / access denied (HTTP {http_code})")
    elif http_code >= 300:
        sec -= 3
        issues.append(f"Redirect chain detected (HTTP {http_code})")

    breakdown["security"] = round(_clamp(sec, 0, 15), 1)

    # ── 5. Content Quality (15 pts) ─────────────────────────────────────────
    content = 15.0
    broken_links: int = audit.broken_links_count or 0
    if broken_links > 0:
        deduction = min(8, broken_links * 2)
        content -= deduction
        issues.append(f"{broken_links} broken link(s) found — hurts SEO and UX")

    broken_images: int = audit.broken_images_count or 0
    if broken_images > 0:
        deduction = min(4, broken_images * 2)
        content -= deduction
        issues.append(f"{broken_images} broken image(s) found")

    console_errors = audit.console_errors_count or 0
    if console_errors > 3:
        content -= 4
        issues.append(f"{console_errors} JavaScript console errors detected")
    elif console_errors > 0:
        content -= 2
        issues.append(f"{console_errors} JavaScript console error(s) detected")

    breakdown["content"] = round(_clamp(content, 0, 15), 1)

    # ── Final ────────────────────────────────────────────────────────────────
    total = round(sum(breakdown.values()), 1)

    return {
        "total_score": total,
        "grade": _grade(total),
        "score_breakdown": breakdown,
        "issues_found": issues,
    }


def score_and_pitch_for_audit(audit_id: int, db: Session) -> Optional[dict]:
    audit = db.query(WebsiteAudit).filter(WebsiteAudit.id == audit_id).first()
    if not audit:
        logger.info(f"[LEAD_SCORE] ⚠️  Audit {audit_id} not found.")
        return None

    if audit.status != "completed":
        logger.info(f"[LEAD_SCORE] ⏭️  Audit {audit_id} status={audit.status}, skipping.")
        return None

    if audit.is_scored:
        logger.info(f"[LEAD_SCORE] ⏭️  Audit {audit_id} already scored, skipping.")
        return None

    client = db.query(Business_Client).filter(
        Business_Client.id == audit.business_client_id
    ).first()

    ## Compute score
    score_data = compute_lead_score(audit)

    lead_score = LeadScore(
        uuid               = str(_uuid.uuid4()),
        business_client_id = audit.business_client_id,
        website_audit_id   = audit.id,
        total_score        = score_data["total_score"],
        grade              = score_data["grade"],
        score_breakdown    = score_data["score_breakdown"],
        issues_found       = score_data["issues_found"],
        scored_at          = datetime.now(timezone.utc),
    )
    db.add(lead_score)
    db.flush()

    ## ── Pitch generation: LinkedIn contacts → email → generic ────────────────
    linkedin_contacts = rank_contacts(
        db.query(LinkedinContact)
        .filter(
            LinkedinContact.business_client_id == audit.business_client_id,
            LinkedinContact.deleted_at.is_(None),
        )
        .all()
    )

    pitches_saved = 0

    if linkedin_contacts:
        api_delay = float(__import__('os').getenv("LLM_API_DELAY_SEC", "1.0"))
        for idx, contact in enumerate(linkedin_contacts):
            # Idempotent: skip if this contact already has a pitch for this score
            already_pitched = db.query(SalesPitch).filter(
                and_(
                    SalesPitch.lead_score_id       == lead_score.id,
                    SalesPitch.linkedin_contact_id == contact.id,
                )
            ).first()
            if already_pitched:
                logger.info(f"[LEAD_SCORE] ⏭️  Already pitched contact {contact.name} — skipping.")
                continue

            pitch_data = generate_sales_pitch(
                client           = client,
                audit            = audit,
                score_data       = score_data,
                linkedin_contact = contact,
            )

            db.add(SalesPitch(
                uuid                = str(_uuid.uuid4()),
                business_client_id  = audit.business_client_id,
                lead_score_id       = lead_score.id,
                linkedin_contact_id = contact.id,
                pitch_subject       = pitch_data.get("pitch_subject"),
                pitch_hook          = pitch_data.get("pitch_hook"),
                pitch_body          = pitch_data.get("pitch_body"),
                key_pain_points     = pitch_data.get("key_pain_points"),
                generated_by        = pitch_data.get("generated_by"),
                pitch_channel       = pitch_data.get("pitch_channel", "linkedin"),
                generated_at        = datetime.now(timezone.utc),
            ))
            db.flush()
            pitches_saved += 1

            logger.info(
                f"[LEAD_SCORE] 💬  LinkedIn pitch #{idx + 1} → {contact.name} "
                f"({contact.job_title}) via {pitch_data.get('generated_by')}"
            )

            # Rate-limit delay between consecutive LLM calls
            if idx < len(linkedin_contacts) - 1 and api_delay > 0:
                __import__('time').sleep(api_delay)

    else:
        # No LinkedIn contacts — fall back to email or generic
        contact_email = client.email if client else None
        pitch_data = generate_sales_pitch(
            client        = client,
            audit         = audit,
            score_data    = score_data,
            contact_email = contact_email,
        )
        channel = pitch_data.get("pitch_channel", "email" if contact_email else "generic")

        db.add(SalesPitch(
            uuid                = str(_uuid.uuid4()),
            business_client_id  = audit.business_client_id,
            lead_score_id       = lead_score.id,
            linkedin_contact_id = None,
            pitch_subject       = pitch_data.get("pitch_subject"),
            pitch_hook          = pitch_data.get("pitch_hook"),
            pitch_body          = pitch_data.get("pitch_body"),
            key_pain_points     = pitch_data.get("key_pain_points"),
            generated_by        = pitch_data.get("generated_by"),
            pitch_channel       = channel,
            generated_at        = datetime.now(timezone.utc),
        ))
        db.flush()
        pitches_saved += 1

        logger.info(
            f"[LEAD_SCORE] 📧  {channel.capitalize()} pitch for client "
            f"{audit.business_client_id} via {pitch_data.get('generated_by')}"
        )

    # ── 3. Mark audit as scored ───────────────────────────────────────────
    audit.is_scored = True

    db.commit()
    db.refresh(lead_score)

    logger.info(
        f"[LEAD_SCORE] ✅  Client {audit.business_client_id} | "
        f"Score={score_data['total_score']} Grade={score_data['grade']} | "
        f"{pitches_saved} pitch(es) saved"
    )

    return {
        "audit_id"    : audit.id,
        "client_id"   : audit.business_client_id,
        "total_score" : score_data["total_score"],
        "grade"       : score_data["grade"],
        "pitches_saved": pitches_saved,
    }


def score_and_pitch_for_all(db: Session, force: bool = False) -> dict:
    query = db.query(WebsiteAudit).filter(WebsiteAudit.status == "completed")
    if not force:
        query = query.filter(WebsiteAudit.is_scored == False)

    audits = query.all()
    total = len(audits)

    if total == 0:
        logger.info("[LEAD_SCORE] No unscored audits found.")
        return {"total": 0, "scored": 0, "failed": 0}

    logger.info(f"[LEAD_SCORE] Batch scoring {total} audit(s)...")

    scored = 0
    failed = 0
    import time
    import os
    batch_delay = float(os.getenv("BATCH_SCORING_DELAY_SEC", "4.0"))

    for index, audit in enumerate(audits):
        try:
            result = score_and_pitch_for_audit(audit.id, db)
            if result:
                scored += 1
                if index < total - 1:
                    time.sleep(batch_delay)
        except Exception as exc:
            failed += 1
            logger.info(f"[LEAD_SCORE] ❌  Audit {audit.id} failed: {exc}")

    summary = {"total": total, "scored": scored, "failed": failed}
    logger.info(f"[LEAD_SCORE] Batch done — {summary}")
    return summary


def run_lead_score_job():
    """Sync entry point called by APScheduler."""
    from app.config.database import SessionLocal
    db = SessionLocal()
    try:
        score_and_pitch_for_all(db)
    finally:
        db.close()
