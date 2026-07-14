from fastapi import APIRouter, Depends, Query
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session
from app.config.database import get_db
from app.services.lead_service import run_lead_job, load_config
from app.core.scheduler import get_scheduler
from app.services.lead_service import get_existing_identifiers
from app.models.business_client import Business_Client
from app.utils.response import success, error
from app.config.database import SessionLocal
from app.schemas.pagination_schema import PaginationQuery
from typing import Optional, Literal

router = APIRouter()

@router.get("/generate-leads")
def generate_leads(
    category: str = Query(
        None, description="e.g. restaurants, coffee shops (optional)"
    ),
    city: str = Query(None, description="e.g. New York (optional)"),
    state: str = Query(None, description="e.g. NY (optional)"),
    country: str = Query(None, description="e.g. United States (optional)"),
    db: Session = Depends(get_db),
):
    config = load_config()
    job = {
        "category": category or config.get("category"),
        "city": city or config.get("city"),
        "state": state or config.get("state"),
        "country": country or config.get("country"),
    }
    return run_lead_job(job, db)


@router.get("/scheduled-jobs")
def get_scheduled_jobs():
    scheduler = get_scheduler()
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append(
            {
                "job_id": job.id,
                "next_run_time": str(job.next_run_time),
                "trigger": str(job.trigger),
            }
        )
    return {"scheduled_jobs": jobs}


@router.get("/debug-existing")
def debug_existing(category: str, city: str):
    existing = get_existing_identifiers(category, city)
    return {
        "category": category,
        "city": city,
        "count": len(existing),
        "names": list(existing),
    }


@router.get("/get-lead-data")
def get_lead_data(
    db: Session = Depends(get_db),
    is_approached: Optional[Literal["1", "0"]] = None,
    query: PaginationQuery = Depends(),
):
    db = SessionLocal()
    try:
        base_query = db.query(Business_Client).order_by(Business_Client.created_at.desc())

        if is_approached not in (None, ""):
            base_query = base_query.filter(
                Business_Client.is_approached == is_approached
            )
        
        if query.paginate == "true":
            per_page = query.result_per_page or 10
            page = query.page or 1

            total = base_query.count()
            lead_data = base_query.offset((page - 1) * per_page).limit(per_page).all()
            data = jsonable_encoder(lead_data)

            return success(
                {
                    "items": data,
                    "pagination": {
                        "total": total,
                        "per_page": per_page,
                        "current_page": page,
                        "last_page": (total + per_page - 1) // per_page,
                    },
                },
                "lead data fetched successfully",
            )
        else:
            lead_data = base_query.all()
            data = jsonable_encoder(lead_data)
            return success(
                data,
                "lead data fetched successfully",
            )

    except Exception:
        return error("Whoops ! look like something went wrong")





