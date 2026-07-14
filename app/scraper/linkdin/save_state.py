from app.utils.logger import logger
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from playwright.sync_api import sync_playwright
from app.config.database import SessionLocal
from app.models.profile_setting import ProfileSetting

def main():
    profile_id = int(input("Enter the Profile ID to update (default 1): ") or "1")
    
    db = SessionLocal()
    profile = db.query(ProfileSetting).filter(ProfileSetting.id == profile_id).first()
    if not profile:
        logger.info(f"Profile {profile_id} not found! Creating it...")
        profile = ProfileSetting(id=profile_id, name=f"Profile {profile_id}")
        db.add(profile)
        db.commit()
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        page.goto("https://www.linkedin.com/login")
        logger.info("Please log in manually...")
        input("After login is complete and the LinkedIn homepage loads, press Enter...")

        state = context.storage_state()
        profile.session_state = json.dumps(state)
        db.commit()

        logger.info(f"✅ Session state saved to database for Profile {profile_id}!")
        browser.close()
    db.close()

if __name__ == "__main__":
    main()