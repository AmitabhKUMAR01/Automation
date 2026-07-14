import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, Query, BackgroundTasks
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app.config.database import get_db, SessionLocal
from app.models.business_client import Business_Client
from app.models.lead_score import LeadScore
from app.models.sales_pitch import SalesPitch
from app.models.website_audit import WebsiteAudit
from app.services.lead_scoring_service import (
    score_and_pitch_for_audit,
    score_and_pitch_for_all,
)
from app.services.pitch_reply_service import check_pitch_replies
from app.utils.response import success, error

router = APIRouter(prefix="/lead", tags=["Lead Scoring"])


# ---------------------------------------------------------------------------
# POST /lead/score/trigger
# ---------------------------------------------------------------------------

@router.post("/score/trigger")
def trigger_lead_scoring(
    background_tasks: BackgroundTasks,
    client_uuid: Optional[str] = Query(None, description="Score a single client by UUID"),
    force: bool = Query(False, description="Re-score already scored audits"),
    db: Session = Depends(get_db),
):
    """
    Trigger lead qualification scoring (+ sales pitch generation) as a
    background task. If client_uuid is provided, scores only that client's
    latest completed audit; otherwise scores all unscored completed audits.
    """
    try:
        if client_uuid:
            client = db.query(Business_Client).filter(
                Business_Client.uuid == client_uuid
            ).first()
            if not client:
                return error("Business client not found", status_code=404)

            # Find the latest completed audit for this client
            audit = (
                db.query(WebsiteAudit)
                .filter(
                    WebsiteAudit.business_client_id == client.id,
                    WebsiteAudit.status == "completed",
                )
                .order_by(WebsiteAudit.id.desc())
                .first()
            )
            if not audit:
                return error("No completed audit found for this client", status_code=404)

            if audit.is_scored and not force:
                return error(
                    "Audit already scored. Use ?force=true to re-score.",
                    status_code=409,
                )

            # Reset flag if force
            if force:
                audit.is_scored = False
                db.commit()

            audit_id = audit.id

            def _run_single():
                task_db = SessionLocal()
                try:
                    score_and_pitch_for_audit(audit_id, task_db)
                finally:
                    task_db.close()

            background_tasks.add_task(_run_single)
            return success(
                {
                    "client_uuid": client_uuid,
                    "client_name": client.name,
                    "audit_id"  : audit_id,
                    "force"     : force,
                },
                f"Lead scoring triggered for {client.name}",
            )

        else:
            # Batch: score all unscored completed audits
            if force:
                # Reset all is_scored flags
                db.query(WebsiteAudit).filter(
                    WebsiteAudit.status == "completed",
                    WebsiteAudit.is_scored == True,   # noqa: E712
                ).update({"is_scored": False})
                db.commit()

            pending = db.query(WebsiteAudit).filter(
                WebsiteAudit.status == "completed",
                WebsiteAudit.is_scored == False,       # noqa: E712
            ).count()

            def _run_all():
                task_db = SessionLocal()
                try:
                    score_and_pitch_for_all(task_db, force=False)
                finally:
                    task_db.close()

            background_tasks.add_task(_run_all)
            return success(
                {"audits_queued": pending, "force": force},
                f"Lead scoring triggered for {pending} unscored audit(s)",
            )

    except Exception as exc:
        return error(f"Failed to trigger lead scoring: {exc}")


# ---------------------------------------------------------------------------
# GET /lead/scores
# ---------------------------------------------------------------------------

@router.get("/scores")
def get_lead_scores(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    grade: Optional[str] = Query(None, description="Filter by grade: A | B | C | D | F"),
    db: Session = Depends(get_db),
):
    """Paginated list of all lead scores with basic client info."""
    try:
        q = (
            db.query(LeadScore, Business_Client)
            .join(Business_Client, LeadScore.business_client_id == Business_Client.id)
            .order_by(LeadScore.scored_at.desc())
        )
        if grade:
            q = q.filter(LeadScore.grade == grade.upper())

        total = q.count()
        rows  = q.offset((page - 1) * per_page).limit(per_page).all()

        items = []
        for ls, bc in rows:
            items.append({
                **jsonable_encoder(ls),
                "client": {
                    "uuid"    : bc.uuid,
                    "name"    : bc.name,
                    "url"     : bc.url,
                    "category": bc.category,
                    "city"    : bc.city,
                },
            })

        return success(
            {
                "items": items,
                "pagination": {
                    "total"       : total,
                    "per_page"    : per_page,
                    "current_page": page,
                    "last_page"   : max(1, (total + per_page - 1) // per_page),
                },
            },
            "Lead scores fetched successfully",
        )
    except Exception as exc:
        return error(f"Failed to fetch lead scores: {exc}")


# ---------------------------------------------------------------------------
# GET /lead/scores/{client_uuid}
# ---------------------------------------------------------------------------

@router.get("/scores/{client_uuid}")
def get_lead_score_for_client(
    client_uuid: str,
    db: Session = Depends(get_db),
):
    """Return the latest lead score + sales pitch for a specific client."""
    try:
        client = db.query(Business_Client).filter(
            Business_Client.uuid == client_uuid
        ).first()
        if not client:
            return error("Business client not found", status_code=404)

        lead_score = (
            db.query(LeadScore)
            .filter(LeadScore.business_client_id == client.id)
            .order_by(LeadScore.id.desc())
            .first()
        )
        if not lead_score:
            return error("No lead score found for this client", status_code=404)

        pitch = (
            db.query(SalesPitch)
            .filter(SalesPitch.lead_score_id == lead_score.id)
            .first()
        )

        return success(
            {
                "client": {
                    "uuid"    : client.uuid,
                    "name"    : client.name,
                    "url"     : client.url,
                    "category": client.category,
                    "city"    : client.city,
                },
                "lead_score"  : jsonable_encoder(lead_score),
                "sales_pitch" : jsonable_encoder(pitch) if pitch else None,
            },
            "Lead score and pitch fetched successfully",
        )
    except Exception as exc:
        return error(f"Failed to fetch lead score for client: {exc}")


# ---------------------------------------------------------------------------
# GET /lead/pitches
# ---------------------------------------------------------------------------

@router.get("/pitches")
def get_sales_pitches(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    generated_by: Optional[str] = Query(None, description="Filter by: gemini | rule_engine"),
    db: Session = Depends(get_db),
):
    """Paginated list of all generated sales pitches."""
    try:
        q = (
            db.query(SalesPitch, Business_Client)
            .join(Business_Client, SalesPitch.business_client_id == Business_Client.id)
            .order_by(SalesPitch.generated_at.desc())
        )
        if generated_by:
            q = q.filter(SalesPitch.generated_by == generated_by)

        total = q.count()
        rows  = q.offset((page - 1) * per_page).limit(per_page).all()

        items = []
        for sp, bc in rows:
            items.append({
                **jsonable_encoder(sp),
                "client": {
                    "uuid"    : bc.uuid,
                    "name"    : bc.name,
                    "category": bc.category,
                    "city"    : bc.city,
                },
            })

        return success(
            {
                "items": items,
                "pagination": {
                    "total"       : total,
                    "per_page"    : per_page,
                    "current_page": page,
                    "last_page"   : max(1, (total + per_page - 1) // per_page),
                },
            },
            "Sales pitches fetched successfully",
        )
    except Exception as exc:
        return error(f"Failed to fetch sales pitches: {exc}")


# ---------------------------------------------------------------------------
# GET /lead/pitches/replies
# ---------------------------------------------------------------------------

@router.get("/pitches/replies")
def get_pitch_replies(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    replied: Optional[str] = Query(
        "true",
        description="Filter: 'true' = only replied | 'false' = awaiting reply | 'all' = both",
    ),
    db: Session = Depends(get_db),
):
    try:
        q = (
            db.query(SalesPitch, Business_Client)
            .join(Business_Client, SalesPitch.business_client_id == Business_Client.id)
            .filter(
                SalesPitch.pitch_channel   == "linkedin",
                SalesPitch.delivery_status == "sent",
            )
        )

        replied_lower = (replied or "true").lower()
        if replied_lower == "true":
            q = q.filter(SalesPitch.reply_received == True)   # noqa: E712
        elif replied_lower == "false":
            q = q.filter(SalesPitch.reply_received == False)  # noqa: E712
        # else: 'all' — no extra filter

        q = q.order_by(SalesPitch.replied_at.desc().nullslast(), SalesPitch.delivered_at.desc())

        total = q.count()
        rows  = q.offset((page - 1) * per_page).limit(per_page).all()

        items = []
        for sp, bc in rows:
            items.append({
                "pitch_id"       : sp.id,
                "pitch_uuid"     : sp.uuid,
                "delivery_status": sp.delivery_status,
                "delivered_at"   : sp.delivered_at,
                # ── reply fields ──
                "reply_received" : sp.reply_received,
                "reply_text"     : sp.reply_text,
                "replied_at"     : sp.replied_at,
                "reply_checked_at": sp.reply_checked_at,
                # ── client ──
                "client": {
                    "uuid"    : bc.uuid,
                    "name"    : bc.name,
                    "category": bc.category,
                    "city"    : bc.city,
                },
            })

        return success(
            {
                "filter" : replied_lower,
                "items"  : items,
                "pagination": {
                    "total"       : total,
                    "per_page"    : per_page,
                    "current_page": page,
                    "last_page"   : max(1, (total + per_page - 1) // per_page),
                },
            },
            f"Pitch replies fetched successfully ({total} total)",
        )
    except Exception as exc:
        return error(f"Failed to fetch pitch replies: {exc}")


# ---------------------------------------------------------------------------
# POST /lead/pitches/check-replies
# ---------------------------------------------------------------------------

@router.post("/pitches/check-replies")
def trigger_reply_check(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    try:
        from app.models.sales_pitch import SalesPitch as SP
        from datetime import timedelta, timezone as tz
        import datetime as _dt

        cutoff = _dt.datetime.now(tz.utc) - timedelta(hours=12)
        eligible = (
            db.query(SP)
            .filter(
                SP.pitch_channel   == "linkedin",
                SP.delivery_status == "sent",
                SP.reply_received  == False,   # noqa: E712
                (
                    SP.reply_checked_at.is_(None)
                    | (SP.reply_checked_at < cutoff)
                ),
            )
            .count()
        )

        if eligible == 0:
            return success({"eligible": 0}, "No pitches eligible for reply check right now")

        def _run():
            task_db = SessionLocal()
            try:
                check_pitch_replies(task_db)
            finally:
                task_db.close()

        background_tasks.add_task(_run)

        return success(
            {"pitches_queued": eligible},
            f"Reply check queued for {eligible} pitch(es) — running in background",
        )

    except Exception as exc:
        return error(f"Failed to trigger reply check: {exc}")


# ---------------------------------------------------------------------------
# GET /lead/summary
# ---------------------------------------------------------------------------

@router.get("/summary")
def get_lead_summary(db: Session = Depends(get_db)):
    """Aggregate stats: grade distribution, avg score, pitch counts."""
    try:
        from sqlalchemy import func

        total_scores = db.query(LeadScore).count()
        avg_score    = db.query(func.avg(LeadScore.total_score)).scalar()

        grade_dist = (
            db.query(LeadScore.grade, func.count(LeadScore.id))
            .group_by(LeadScore.grade)
            .all()
        )

        pitch_count = db.query(SalesPitch).count()
        gemini_count = db.query(SalesPitch).filter(
            SalesPitch.generated_by == "gemini"
        ).count()

        return success(
            {
                "total_scored"     : total_scores,
                "avg_score"        : round(avg_score, 1) if avg_score else None,
                "grade_distribution": {row[0]: row[1] for row in grade_dist},
                "pitches": {
                    "total"      : pitch_count,
                    "via_gemini" : gemini_count,
                    "via_rules"  : pitch_count - gemini_count,
                },
            },
            "Lead summary fetched successfully",
        )
    except Exception as exc:
        return error(f"Failed to fetch lead summary: {exc}")
