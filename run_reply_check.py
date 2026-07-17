"""
run_reply_check.py

Trigger the hybrid LinkedIn Reply Check job manually.
Phase 1: Scans the LinkedIn inbox for unread conversations.
Phase 2: Opens threads only for contacts that have new messages.

Run from project root:
    venv/bin/python run_reply_check.py
    venv/bin/python run_reply_check.py --profile-id 2
"""
import sys
import os
import argparse

# ── Make sure project root is on the path ─────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.services.pitch_reply_service import run_pitch_reply_check_job
from app.utils.logger import logger


def main():
    parser = argparse.ArgumentParser(
        description="Manual trigger for the hybrid LinkedIn Reply Check."
    )
    parser.add_argument(
        "--profile-id",
        type=int,
        default=1,
        help="ProfileSetting ID to use for the check (default: 1)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  LinkedIn Reply Check — Manual Trigger (Hybrid)")
    print("=" * 60)
    print(f"  Profile ID : {args.profile_id}")
    print("  Phase 1: Scan inbox for unread conversations")
    print("  Phase 2: Open threads for matched contacts only")
    print("  Press Ctrl+C at any time to abort.\n")

    try:
        run_pitch_reply_check_job(profile_id=args.profile_id)
        print("\n✅ Reply check completed. Check app logs for details.")
    except KeyboardInterrupt:
        print("\n⛔ Aborted by user.")
    except Exception as exc:
        print(f"\n❌ Error: {exc}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
