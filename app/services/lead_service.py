from app.utils.logger import logger
from sqlalchemy.orm import Session
from app.scraper.maps_scraper import scrape_google_maps
from app.models.business_client import Business_Client
from app.models.scrape_session import ScrapeSession
from app.models.scraped_raw_data import ScrapedRawData
from app.models.scraper_job import ScraperJob
from app.models.scraper_setting import ScraperSetting
import re
import json
import os
import uuid
from datetime import datetime, timezone, date
from app.config.database import SessionLocal
from sqlalchemy import func

CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "config", "scraper_settings.json"
)


def load_config() -> dict:
    db = SessionLocal()
    try:
        # Check if we need to seed
        jobs_count = db.query(ScraperJob).count()
        if jobs_count == 0 and os.path.exists(CONFIG_PATH):
            logger.info("[CONFIG] Database is empty, seeding from JSON...")
            seed_db_from_config()
            db.expire_all() # Ensure we get fresh data after seeding

        # Load jobs
        jobs = db.query(ScraperJob).filter(ScraperJob.is_active).all()
        job_list = []
        for j in jobs:
            job_list.append({
                "category": j.category,
                "city": j.city,
                "state": j.state,
                "country": j.country,
                "lat": j.lat,
                "long": j.long
            })
        
        # Load settings
        setting = db.query(ScraperSetting).filter(ScraperSetting.setting_key == "schedule_times").first()
        schedule_times = ["08:00"]
        if setting:
            try:
                schedule_times = json.loads(setting.setting_value)
            except Exception:
                schedule_times = [setting.setting_value]
        
        return {
            "schedule_times": schedule_times,
            "jobs": job_list
        }
    finally:
        db.close()


def seed_db_from_config():
    if not os.path.exists(CONFIG_PATH):
        return
    
    with open(CONFIG_PATH, "r") as f:
        data = json.load(f)
    
    db = SessionLocal()
    try:
        # Seed Settings
        if "schedule_times" in data:
            setting = db.query(ScraperSetting).filter(ScraperSetting.setting_key == "schedule_times").first()
            if not setting:
                setting = ScraperSetting(
                    setting_key="schedule_times",
                    setting_value=json.dumps(data["schedule_times"])
                )
                db.add(setting)
        
        # Seed Jobs
        for j in data.get("jobs", []):
            existing = db.query(ScraperJob).filter(
                ScraperJob.category == j.get("category"),
                ScraperJob.city == j.get("city")
            ).first()
            if not existing:
                job = ScraperJob(
                    category=j.get("category"),
                    city=j.get("city"),
                    state=j.get("state"),
                    country=j.get("country"),
                    lat=j.get("lat"),   # might be null in JSON initially
                    long=j.get("long")  # might be null in JSON initially
                )
                db.add(job)
        
        db.commit()
        logger.info("[CONFIG] Seeding completed.")
    except Exception as e:
        db.rollback()
        logger.info(f"[CONFIG] Seeding failed: {e}")
    finally:
        db.close()


def get_existing_identifiers(category: str, city: str) -> set:
    ## Fetch existing business names only for the same category and city.
    db = SessionLocal()
    try:
        records = db.query(Business_Client.name).filter(
            Business_Client.category == category.title(),
            Business_Client.city     == city.title()
        ).all()
        return {r.name for r in records}
    finally:
        db.close()


def parse_zip_code(address: str) -> str:
    if address:
        match = re.search(r"\b\d{5}(?:-\d{4})?\b", address)
        return match.group(0) if match else None
    return None


def run_lead_job(job: dict, db: Session) -> dict:
    category = job.get("category")
    city     = job.get("city")
    state    = job.get("state")
    country  = job.get("country")

    if not category or not city:
        return {"error": "Category and city are required."}

    location_str = ", ".join(filter(None, [city, state, country]))
    maps_query   = f"{category} in {location_str}"

    ## Create a scrape-session monitor record
    started_at = datetime.now(timezone.utc)
    session_log = ScrapeSession(
        uuid       = str(uuid.uuid4()),
        query      = maps_query,
        category   = category.title(),
        city       = city.title(),
        state      = state.upper()   if state   else None,
        country    = country.title() if country else None,
        started_at = started_at,
        status     = "running",
    )
    db.add(session_log)
    db.flush()          # get session_log.id without full commit

    try:
        existing   = get_existing_identifiers(category, city)
        businesses = scrape_google_maps(job, existing)

        results = {
            "query"              : maps_query,
            "scraped"            : len(businesses),
            "saved"              : 0,
            "skipped_duplicates" : 0,
        }

        for item in businesses:
            name    = item.get("name")
            phone   = item.get("phone")
            address = item.get("address")

            existing_record = db.query(Business_Client).filter(
                Business_Client.name == name
            ).first()
            if existing_record and (
                (phone   and existing_record.phone   == phone) or
                (address and existing_record.address == address)
            ):
                results["skipped_duplicates"] += 1
                continue

            client = Business_Client(
                name         = name,
                url          = item.get("url"),
                phone        = phone,
                email        = item.get("email"),
                address      = address,
                category     = category.title(),
                scrape_source= "google_maps",
                social_links = item.get("social_links", {}),
                meta_data    = {
                    "rating": item.get("rating"),
                    "about": item.get("about")
                },
                country      = country.title() if country else None,
                state        = state.upper()   if state   else None,
                city         = city.title(),
                zip_code     = parse_zip_code(address),
            )
            db.add(client)
            db.flush() 
            results["saved"] += 1

            ## Save raw data
            raw_data = ScrapedRawData(
                business_client_id = client.id, 
                data               = item,
                scrape_source      = "google_maps",
                search_engine      = "Google",
                browser            = "Chromium",
            )
            db.add(raw_data)
            db.flush()

        ## Finalise the session record
        finished_at = datetime.now(timezone.utc)
        session_log.finished_at       = finished_at
        session_log.duration_sec      = round((finished_at - started_at).total_seconds(), 2)
        session_log.total_scraped     = results["scraped"]
        session_log.total_saved       = results["saved"]
        session_log.skipped_duplicates = results["skipped_duplicates"]
        session_log.status            = "completed"

        db.commit()
        results["duration_sec"]    = session_log.duration_sec
        results["session_uuid"]    = session_log.uuid
        return results

    except Exception as exc:
        ## Mark session as failed
        finished_at = datetime.now(timezone.utc)
        session_log.finished_at  = finished_at
        session_log.duration_sec = round((finished_at - started_at).total_seconds(), 2)
        session_log.status       = "failed"
        session_log.error        = str(exc)[:1000]
        db.commit()
        raise


## Fetch all Business_Client rows created today 
def get_todays_records() -> list[dict]:
    db = SessionLocal()
    try:
        today = date.today()
        records = (
            db.query(Business_Client)
            .filter(func.date(Business_Client.created_at) == today)
            .all()
        )
        
        results = []
        for r in records:
            meta = r.meta_data or {}
            about_data = meta.get("about")
            
            if isinstance(about_data, dict):
                about_text = about_data.get("description", "")
            elif isinstance(about_data, list):
                about_text = ", ".join(about_data)
            else:
                about_text = str(about_data) if about_data else ""

            results.append({
                "Name"      : r.name,
                "Phone"     : r.phone        or "",
                "Email"     : r.email        or "",
                "Address"   : r.address      or "",
                "Website"   : r.url          or "",
                "Category"  : r.category     or "",
                "City"      : r.city         or "",
                "State"     : r.state        or "",
                "Country"   : r.country      or "",
                "Zip Code"  : r.zip_code     or "",
                "Rating"    : meta.get("rating") or "",
                "About"     : about_text,
                "Facebook"  : (r.social_links or {}).get("facebook")  or "",
                "Instagram" : (r.social_links or {}).get("instagram") or "",
                "LinkedIn"  : (r.social_links or {}).get("linkedin")  or "",
                "Twitter"   : (r.social_links or {}).get("twitter")   or "",
                "WhatsApp"  : (r.social_links or {}).get("whatsapp")  or "",
                "YouTube"   : (r.social_links or {}).get("youtube")   or "",
                "TikTok"    : (r.social_links or {}).get("tiktok")    or "",
                "Scraped At": r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
            })
        return results
    finally:
        db.close()
