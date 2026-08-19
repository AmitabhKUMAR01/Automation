from fastapi import FastAPI
from app.router import business_client
from app.router import website_audit
from app.router import lead_score
from app.router import linkedin_router
from app.router import profile_setting
from app.router import profile_router
from app.core.scheduler import schedule_jobs, get_scheduler
import app.models.website_audit
import app.models.lead_score
import app.models.sales_pitch
import app.models.linkedin_contact # registers telegram_pending_actions table

app = FastAPI(title="API")

app.include_router(business_client.router)
app.include_router(website_audit.router)
app.include_router(lead_score.router)
app.include_router(linkedin_router.router)
app.include_router(profile_setting.router)
app.include_router(profile_router.router)


@app.on_event("startup")
def startup_scheduler():
    schedule_jobs()

@app.on_event("shutdown")
def shutdown_scheduler():
    scheduler = get_scheduler()
    if scheduler:
        scheduler.shutdown()


@app.get("/")
def read_root():
    return {"status": 200, "message": "Server is running"}
