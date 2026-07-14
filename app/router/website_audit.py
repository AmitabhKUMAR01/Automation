import asyncio
from typing import Optional
from fastapi import APIRouter, Depends, Query, BackgroundTasks
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.config.database import get_db, SessionLocal
from app.models.website_audit import WebsiteAudit
from app.models.business_client import Business_Client
from app.services.website_audit_service import audit_website, run_audit_for_all_clients
from app.services.lead_scoring_service import score_and_pitch_for_audit, score_and_pitch_for_all
from app.utils.response import success, error

router = APIRouter(prefix="/audit", tags=["Website Audit"])

@router.get("/results")
def get_audit_results(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None, description="Filter by status: pending | running | completed | failed"),
    db: Session = Depends(get_db),
):
    try:
        q = db.query(WebsiteAudit).order_by(WebsiteAudit.created_at.desc())
        if status:
            q = q.filter(WebsiteAudit.status == status)

        total = q.count()
        items = q.offset((page - 1) * per_page).limit(per_page).all()
        data  = jsonable_encoder(items)

        return success(
            {
                "items": data,
                "pagination": {
                    "total": total,
                    "per_page": per_page,
                    "current_page": page,
                    "last_page": max(1, (total + per_page - 1) // per_page),
                },
            },
            "Audit results fetched successfully",
        )
    except Exception as exc:
        return error(f"Failed to fetch audit results: {exc}")

@router.get("/results/{client_uuid}")
def get_audit_for_client(
    client_uuid: str,
    db: Session = Depends(get_db),
):
    try:
        client = db.query(Business_Client).filter(Business_Client.uuid == client_uuid).first()
        if not client:
            return error("Business client not found", status_code=404)

        audits = (
            db.query(WebsiteAudit)
            .filter(WebsiteAudit.business_client_id == client.id)
            .order_by(WebsiteAudit.created_at.desc())
            .all()
        )
        return success(
            {
                "client": {"uuid": client.uuid, "name": client.name, "url": client.url},
                "audits": jsonable_encoder(audits),
                "total_audits": len(audits),
            },
            "Client audit history fetched successfully",
        )
    except Exception as exc:
        return error(f"Failed to fetch audit for client: {exc}")


# ----------------------------------------------------------------
# POST /audit/trigger — manually run audit (all or single client)
# ----------------------------------------------------------------
@router.post("/trigger")
def trigger_audit(
    background_tasks: BackgroundTasks,
    client_uuid: Optional[str] = Query(None, description="Run audit for a single client by UUID"),
    force: bool = Query(False, description="If true, re-audit clients even if is_audited=True"),
    db: Session = Depends(get_db),
):
    try:
        if client_uuid:
            client = db.query(Business_Client).filter(Business_Client.uuid == client_uuid).first()
            if not client:
                return error("Business client not found", status_code=404)
            if not client.url:
                return error("This client has no URL to audit", status_code=400)

            def _run_single():
                task_db = SessionLocal()
                try:
                    result = asyncio.run(audit_website(client.id, client.url, task_db))
                    # Auto-score immediately after audit completes
                    if result and result.status == "completed":
                        score_and_pitch_for_audit(result.id, task_db)
                finally:
                    task_db.close()

            background_tasks.add_task(_run_single)
            return success(
                {
                    "client_uuid": client_uuid,
                    "url": client.url,
                    "was_audited": client.is_audited,
                },
                f"Audit triggered for {client.name}",
            )

        else:
            def _run_all():
                task_db = SessionLocal()
                try:
                    asyncio.run(run_audit_for_all_clients(task_db, force=force))
                    # Auto-score all newly completed audits
                    score_and_pitch_for_all(task_db, force=False)
                finally:
                    task_db.close()

            background_tasks.add_task(_run_all)

            base_q = db.query(Business_Client).filter(
                Business_Client.url.isnot(None),
                Business_Client.url != "",
            )
            total_with_url = base_q.count()
            pending        = base_q.filter(Business_Client.is_audited == False).count()  # noqa: E712
            already_done   = total_with_url - pending

            queued = total_with_url if force else pending
            return success(
                {
                    "clients_queued": queued,
                    "already_audited": already_done,
                    "force": force,
                },
                f"Audit triggered for {queued} client(s) "
                f"({'all' if force else 'pending only — use ?force=true to re-audit all'})",
            )

    except Exception as exc:
        return error(f"Failed to trigger audit: {exc}")

@router.get("/summary")
def get_audit_summary(db: Session = Depends(get_db)):
    try:
        completed = db.query(WebsiteAudit).filter(WebsiteAudit.status == "completed")

        total_audited    = completed.count()
        avg_load_time    = db.query(func.avg(WebsiteAudit.load_time_ms)).filter(WebsiteAudit.status == "completed").scalar()
        total_broken_links  = db.query(func.sum(WebsiteAudit.broken_links_count)).filter(WebsiteAudit.status == "completed").scalar()
        total_broken_images = db.query(func.sum(WebsiteAudit.broken_images_count)).filter(WebsiteAudit.status == "completed").scalar()
        total_console_errors = db.query(func.sum(WebsiteAudit.console_errors_count)).filter(WebsiteAudit.status == "completed").scalar()

        # SEO pass rate: has title + has description
        with_title = completed.filter(WebsiteAudit.seo_title.isnot(None)).count()
        with_desc  = completed.filter(WebsiteAudit.seo_description.isnot(None)).count()
        with_h1    = completed.filter(WebsiteAudit.has_h1 == True).count()

        # Status breakdown
        status_breakdown = (
            db.query(WebsiteAudit.status, func.count(WebsiteAudit.id))
            .group_by(WebsiteAudit.status)
            .all()
        )

        return success(
            {
                "total_audited": total_audited,
                "avg_load_time_ms": round(avg_load_time, 2) if avg_load_time else None,
                "total_broken_links": int(total_broken_links or 0),
                "total_broken_images": int(total_broken_images or 0),
                "total_console_errors": int(total_console_errors or 0),
                "seo": {
                    "with_title": with_title,
                    "with_description": with_desc,
                    "with_h1": with_h1,
                    "title_pass_rate": f"{round(with_title / total_audited * 100, 1)}%" if total_audited else "N/A",
                    "desc_pass_rate": f"{round(with_desc / total_audited * 100, 1)}%" if total_audited else "N/A",
                },
                "status_breakdown": {row[0]: row[1] for row in status_breakdown},
            },
            "Audit summary fetched successfully",
        )
    except Exception as exc:
        return error(f"Failed to fetch audit summary: {exc}")
