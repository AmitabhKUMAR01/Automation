#!/usr/bin/env python3
"""
LinkedIn Browser Launcher
=========================
Opens a Chromium browser pre-loaded with your saved LinkedIn session.

Usage:
    python linkedin_login.py              # uses profile ID 1
    python linkedin_login.py --profile 2  # uses profile ID 2
"""

import argparse
import json
import os
import sys

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.config.database import SessionLocal
from app.models.profile_setting import ProfileSetting
from app.utils.logger import logger

load_dotenv()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Open LinkedIn in a browser using a saved session.",
    )
    parser.add_argument(
        "--profile",
        type=int,
        default=1,
        help="Profile ID to load from the profile_settings table (default: 1).",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    profile_id = args.profile

    db = SessionLocal()

    try:
        profile = db.query(ProfileSetting).filter(ProfileSetting.id == profile_id).first()
        if not profile:
            logger.info(f"❌ Profile {profile_id} not found in the database.")
            return
        if not profile.session_state:
            logger.info(f"❌ Profile {profile_id} ('{profile.name}') has no saved session.")
            return

        session_dict = json.loads(profile.session_state)
        logger.info(f"✅ Loaded session for Profile {profile_id} ('{profile.name}')")

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = browser.new_context(
                storage_state=session_dict,
                viewport={"width": 1400, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()
            page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")

            logger.info("🌐 LinkedIn is open — browse freely.")
            print()
            print("=" * 50)
            print("  LinkedIn is open in the browser.")
            print("  Press ENTER here to close when you're done.")
            print("=" * 50)
            input()

            browser.close()
            logger.info("🔒 Browser closed.")

    except json.JSONDecodeError:
        logger.info(f"❌ Profile {profile_id} session state is invalid JSON.")
    except KeyboardInterrupt:
        logger.info("\n🛑 Cancelled.")
    except Exception as exc:
        logger.info(f"❌ Error: {exc}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
