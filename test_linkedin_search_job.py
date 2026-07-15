"""
test_linkedin_search_job.py

Manual trigger script to test the LinkedIn Search + Connect job.
Run from the project root:

    venv/bin/python test_linkedin_search_job.py
    venv/bin/python test_linkedin_search_job.py --profile-id 2
    venv/bin/python test_linkedin_search_job.py --dry-run        # no browser, just config check
"""

import argparse
import sys
import os

# ── Make sure project root is on the path ─────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.config.database import SessionLocal
from app.models.linkedin_search_config import LinkedinSearchConfig
from app.models.linkedin_search_contact import LinkedinSearchContact
from app.models.profile_setting import ProfileSetting
from app.utils.logger import logger


def print_separator(title: str = ""):
    print("\n" + "=" * 60)
    if title:
        print(f"  {title}")
        print("=" * 60)


def check_config(db) -> LinkedinSearchConfig | None:
    """Load and display the active LinkedinSearchConfig."""
    print_separator("1. LinkedIn Search Config")
    config = (
        db.query(LinkedinSearchConfig)
        .filter(LinkedinSearchConfig.is_active == True)  # noqa: E712
        .first()
    )
    if not config:
        print("❌  No active LinkedinSearchConfig row found.")
        print("    Insert a row first — see the SQL command below:\n")
        print(
            "    INSERT INTO linkedin_search_configs "
            "(positions, location, schedule_time, timezone, is_active)\n"
            '    VALUES (\'["CEO", "CTO", "Director"]\', \'Bahrain\', \'14:00\', \'IST\', 1);\n'
        )
        return None

    print(f"  ✅ Config found (id={config.id})")
    print(f"     Positions     : {config.positions}")
    print(f"     Location      : {config.location}")
    print(f"     Schedule time : {config.schedule_time} {config.timezone}")
    print(f"     Is active     : {config.is_active}")
    return config


def check_profile(db, profile_id: int) -> ProfileSetting | None:
    """Load and display the target ProfileSetting."""
    print_separator("2. LinkedIn Profile (Session)")
    profile = db.query(ProfileSetting).filter(ProfileSetting.id == profile_id).first()
    if not profile:
        print(f"❌  ProfileSetting id={profile_id} not found.")
        return None

    has_session = bool(profile.session_state)
    print(f"  {'✅' if has_session else '❌'}  Profile found (id={profile.id}, name={profile.name})")
    print(f"     Is active      : {profile.is_active}")
    print(f"     Session state  : {'present ✅' if has_session else 'MISSING ❌ — run save_state.py first'}")
    print(f"     Last used at   : {profile.last_used_at}")

    if not has_session:
        return None
    return profile


def check_existing_contacts(db):
    """Show how many contacts we've already sent connections to."""
    print_separator("3. Existing linkedin_search_contacts")
    total   = db.query(LinkedinSearchContact).count()
    search  = db.query(LinkedinSearchContact).filter(LinkedinSearchContact.source == "search").count()
    network = db.query(LinkedinSearchContact).filter(LinkedinSearchContact.source == "network").count()
    print(f"  Total contacts tracked : {total}")
    print(f"  From search results    : {search}")
    print(f"  From My Network tab    : {network}")

    recent = (
        db.query(LinkedinSearchContact)
        .order_by(LinkedinSearchContact.created_at.desc())
        .limit(5)
        .all()
    )
    if recent:
        print("\n  Last 5 contacts:")
        for c in recent:
            print(f"    [{c.source:7}] {(c.name or 'N/A'):<30} | {c.position or '':<20} | {c.profile_url[:50]}")


def run_job(profile_id: int):
    """Trigger the actual LinkedIn search + connect job."""
    from app.scraper.linkedin_search_connector import run_linkedin_search_and_send_connections
    from app.config.database import SessionLocal
    from app.models.linkedin_search_config import LinkedinSearchConfig

    print_separator("4. Running LinkedIn Search + Connect Job")
    print(f"  Profile ID : {profile_id}")
    print("  This will open a browser and may send real connection requests.")
    print("  Press Ctrl+C at any time to abort.\n")

    db = SessionLocal()
    try:
        config = (
            db.query(LinkedinSearchConfig)
            .filter(LinkedinSearchConfig.is_active == True)  # noqa: E712
            .first()
        )
        positions = config.positions or []
        location  = config.location  or "Bahrain"

        print(f"  Searching for: {positions}")
        print(f"  Location     : {location}")
        print(f"  Search limit : {os.getenv('SEARCH_CONNECT_LIMIT', '19')} connections")
        print(f"  Network limit: {os.getenv('NETWORK_CONNECT_LIMIT', '20')} connections\n")

        result = run_linkedin_search_and_send_connections(
            positions  = positions,
            location   = location,
            profile_id = profile_id,
            db         = db,
        )

        print_separator("Job Result")
        print(f"  Search connections sent  : {result['search_connections_sent']}")
        print(f"  Network connections sent : {result['network_connections_sent']}")
        print(f"  Session expired          : {result['session_expired']}")
        print(f"  Weekly limit reached     : {result.get('weekly_limit_reached', False)}")
        if result.get("weekly_limit_reached"):
            print("\n  ⛔ LinkedIn blocked: weekly invite limit hit. Try again next week.")
        if result.get("error"):
            print(f"  ❌ Error                 : {result['error']}")

    except KeyboardInterrupt:
        print("\n⛔  Aborted by user.")
        return
    except Exception as exc:
        import traceback
        print(f"\n❌  Unexpected error: {exc}")
        traceback.print_exc()
        return
    finally:
        db.close()

    print_separator("5. Results — linkedin_search_contacts")
    db2 = SessionLocal()
    try:
        check_existing_contacts(db2)
    finally:
        db2.close()


def main():
    parser = argparse.ArgumentParser(
        description="Manual trigger for the LinkedIn Search + Connect job."
    )
    parser.add_argument(
        "--profile-id",
        type=int,
        default=1,
        help="ProfileSetting ID to use for the session (default: 1)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only check config and profile — do NOT open a browser or send connections.",
    )
    args = parser.parse_args()

    print_separator("LinkedIn Search + Connect — Test Runner")
    print(f"  Profile ID : {args.profile_id}")
    print(f"  Mode       : {'DRY RUN (no browser)' if args.dry_run else 'LIVE (will send connections)'}")

    db = SessionLocal()
    try:
        config  = check_config(db)
        profile = check_profile(db, args.profile_id)
        check_existing_contacts(db)
    finally:
        db.close()

    if not config:
        print("\n❌  Cannot proceed — fix the config issue above.")
        sys.exit(1)

    if not profile:
        print("\n❌  Cannot proceed — fix the profile/session issue above.")
        sys.exit(1)

    if args.dry_run:
        print_separator("Dry Run Complete ✅")
        print("  Everything looks good. Remove --dry-run to run for real.")
        return

    # ── Confirm before going live ──────────────────────────────────────────────
    print_separator("⚠️  Confirm Live Run")
    print(f"  Will search for: {config.positions}")
    print(f"  In location    : {config.location}")
    print(f"  Using profile  : {profile.name} (id={profile.id})")
    print(f"\n  Up to 19 connections via search + 20 via My Network will be sent.")
    confirm = input("\n  Type 'yes' to proceed: ").strip().lower()

    if confirm != "yes":
        print("  Aborted.")
        sys.exit(0)

    run_job(args.profile_id)


if __name__ == "__main__":
    main()
