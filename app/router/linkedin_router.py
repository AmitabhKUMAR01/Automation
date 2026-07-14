from typing import Optional
from fastapi import APIRouter, Depends, BackgroundTasks, Query
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.services.linkedin_service import run_linkedin_batch_job
from app.config.database import get_db, SessionLocal
from app.models.business_client import Business_Client
from app.models.linkedin_contact import LinkedinContact
from app.services.linkedin_service import run_linkedin_enrichment, run_linkedin_connections
from app.utils.response import success, error

router = APIRouter(prefix="/linkedin", tags=["LinkedIn"])

@router.post("/search/batch")
def trigger_linkedin_batch(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    try:
        pending_count = (
            db.query(Business_Client)
            .filter(Business_Client.is_linkedin_searched == False)  # noqa: E712
            .count()
        )

        if pending_count == 0:
            return success({"queued": 0}, "No pending clients to search")

        def _run():
            run_linkedin_batch_job()

        background_tasks.add_task(_run)

        return success(
            {"clients_queued": pending_count},
            f"LinkedIn batch search queued for {pending_count} client(s)",
        )

    except Exception as exc:
        return error(f"Failed to trigger batch search: {exc}")


@router.post("/search/{client_uuid}")
def trigger_linkedin_search(
    client_uuid: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    try:
        client = db.query(Business_Client).filter(Business_Client.uuid == client_uuid).first()
        if not client:
            return error("Business client not found", status_code=404)

        if client.is_linkedin_searched:
            return success(
                {"status": client.linkedin_search_status, "is_linkedin_searched": True},
                f"Already searched — status: {client.linkedin_search_status}",
            )

        def _run():
            task_db = SessionLocal()
            try:
                run_linkedin_enrichment(client.id, task_db)
            finally:
                task_db.close()

        background_tasks.add_task(_run)

        return success(
            {"client_uuid": client_uuid, "client_name": client.name},
            f"LinkedIn search queued for '{client.name}'",
        )

    except Exception as exc:
        return error(f"Failed to trigger LinkedIn search: {exc}")



@router.post("/connect/{client_uuid}")
def trigger_send_connections(
    client_uuid: str,
    background_tasks: BackgroundTasks,
    limit: int = Query(5, ge=1, le=20, description="Max connection requests to send"),
    message: Optional[str] = Query(None, description="Optional note to include with request"),
    db: Session = Depends(get_db),
):
    """
    Send LinkedIn connection requests to saved contacts for a client.
    Processes highest-confidence contacts first, up to the limit.
    Returns immediately; sending runs in the background.
    """
    try:
        client = db.query(Business_Client).filter(Business_Client.uuid == client_uuid).first()
        if not client:
            return error("Business client not found", status_code=404)

        # Count how many pending contacts exist
        pending = (
            db.query(LinkedinContact)
            .filter(
                LinkedinContact.business_client_id == client.id,
                LinkedinContact.connection_sent    == False,  # noqa: E712
            )
            .count()
        )

        if pending == 0:
            return success(
                {"pending_contacts": 0},
                "No pending contacts to connect with",
            )

        def _run():
            task_db = SessionLocal()
            try:
                run_linkedin_connections(client.id, task_db, limit=limit, message=message)
            finally:
                task_db.close()

        background_tasks.add_task(_run)

        return success(
            {
                "client_uuid":      client_uuid,
                "client_name":      client.name,
                "pending_contacts": pending,
                "will_attempt":     min(limit, pending),
                "with_note":        bool(message),
            },
            f"Connection requests queued for '{client.name}' — will attempt {min(limit, pending)}",
        )

    except Exception as exc:
        return error(f"Failed to queue connection requests: {exc}")


@router.get("/contacts/{client_uuid}")
def get_linkedin_contacts(
    client_uuid: str,
    confidence: Optional[str] = Query(None, description="Filter by confidence: high | medium | low"),
    connection_sent: Optional[bool] = Query(None, description="Filter by connection_sent status"),
    db: Session = Depends(get_db),
):
    """
    List all LinkedIn contacts found for a business client.
    """
    try:
        client = db.query(Business_Client).filter(Business_Client.uuid == client_uuid).first()
        if not client:
            return error("Business client not found", status_code=404)

        q = db.query(LinkedinContact).filter(LinkedinContact.business_client_id == client.id)

        if confidence:
            q = q.filter(LinkedinContact.match_confidence == confidence)
        if connection_sent is not None:
            q = q.filter(LinkedinContact.connection_sent == connection_sent)

        contacts = q.order_by(
            # high first, then medium, then low
            LinkedinContact.match_confidence.asc(),
            LinkedinContact.created_at.desc()
        ).all()

        return success(
            {
                "client": {
                    "uuid":                  client.uuid,
                    "name":                  client.name,
                    "is_linkedin_searched":  client.is_linkedin_searched,
                    "linkedin_search_status": client.linkedin_search_status,
                },
                "contacts":       jsonable_encoder(contacts),
                "total_contacts": len(contacts),
                "connections_sent": sum(1 for c in contacts if c.connection_sent),
            },
            "Contacts fetched successfully",
        )

    except Exception as exc:
        return error(f"Failed to fetch contacts: {exc}")

@router.get("/status")
def get_linkedin_status(db: Session = Depends(get_db)):
    """
    Dashboard: breakdown of linkedin_search_status across all clients,
    and total connection stats.
    """
    try:
        # Status breakdown
        status_rows = (
            db.query(Business_Client.linkedin_search_status, func.count(Business_Client.id))
            .group_by(Business_Client.linkedin_search_status)
            .all()
        )
        status_breakdown = {row[0] or "pending": row[1] for row in status_rows}

        total_clients  = db.query(Business_Client).count()
        searched_count = db.query(Business_Client).filter(Business_Client.is_linkedin_searched == True).count()  # noqa: E712
        pending_count  = total_clients - searched_count

        # Contact stats
        total_contacts     = db.query(LinkedinContact).count()
        connections_sent   = db.query(LinkedinContact).filter(LinkedinContact.connection_sent == True).count()  # noqa: E712
        high_confidence    = db.query(LinkedinContact).filter(LinkedinContact.match_confidence == "high").count()
        medium_confidence  = db.query(LinkedinContact).filter(LinkedinContact.match_confidence == "medium").count()
        low_confidence     = db.query(LinkedinContact).filter(LinkedinContact.match_confidence == "low").count()

        return success(
            {
                "clients": {
                    "total":           total_clients,
                    "searched":        searched_count,
                    "pending_search":  pending_count,
                    "status_breakdown": status_breakdown,
                },
                "contacts": {
                    "total":           total_contacts,
                    "connections_sent": connections_sent,
                    "pending_connect": total_contacts - connections_sent,
                    "by_confidence": {
                        "high":   high_confidence,
                        "medium": medium_confidence,
                        "low":    low_confidence,
                    },
                },
            },
            "LinkedIn status fetched successfully",
        )

    except Exception as exc:
        return error(f"Failed to fetch LinkedIn status: {exc}")
