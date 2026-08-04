"""
export_linkedin_contacts_backfill.py
─────────────────────────────────────────────────────────────────────────────
Standalone script — export ALL previous LinkedIn contacts to Google Sheets,
one tab per calendar date, grouped by created_at date.

By default it only exports rows that have never been exported (exported_at IS NULL).
Use --force to re-export a specific date even if already marked.

Usage
─────
    # Export all un-exported historical data
    python export_linkedin_contacts_backfill.py

    # Dry-run: see what would be exported without writing anything
    python export_linkedin_contacts_backfill.py --dry-run

    # Force re-export a specific date (overwrites existing sheet tab)
    python export_linkedin_contacts_backfill.py --date 2026-07-27 --force

    # Export a specific date, skipping the already-exported check
    python export_linkedin_contacts_backfill.py --date 2026-07-27

Options
───────
    --dry-run       Print a summary of what would be exported; make no changes.
    --date YYYY-MM-DD   Only process a single date instead of all un-exported dates.
    --force         Include rows already marked as exported (re-exports that date).
"""

import argparse
import os
import sys
from datetime import date, datetime, timezone

# ── Bootstrap: load .env and project path ────────────────────────────────────
from dotenv import load_dotenv
load_dotenv()                           # reads .env from current working directory

# Make sure `app` package is importable when running from the project root
sys.path.insert(0, os.path.dirname(__file__))

import gspread
from sqlalchemy import func, distinct

from app.config.database import SessionLocal
from app.models.linkedin_search_contact import LinkedinSearchContact
from app.services.sheet_exporter import (
    get_google_creds,
    _get_linkedin_contacts_for_date,
    _mark_contacts_exported,
    _write_contacts_to_sheet,
)
from app.utils.logger import logger


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_unexported_dates() -> list[date]:
    """Return a sorted list of distinct calendar dates that have un-exported contacts."""
    db = SessionLocal()
    try:
        rows = (
            db.query(func.date(LinkedinSearchContact.created_at).label("day"))
            .filter(LinkedinSearchContact.exported_at.is_(None))
            .distinct()
            .order_by("day")
            .all()
        )
        return [row.day for row in rows]
    finally:
        db.close()


def get_all_dates() -> list[date]:
    """Return a sorted list of all distinct calendar dates that have any contacts."""
    db = SessionLocal()
    try:
        rows = (
            db.query(func.date(LinkedinSearchContact.created_at).label("day"))
            .distinct()
            .order_by("day")
            .all()
        )
        return [row.day for row in rows]
    finally:
        db.close()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Backfill LinkedIn contacts to Google Sheets (one tab per date)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be exported without writing to the sheet or marking rows.",
    )
    parser.add_argument(
        "--date",
        metavar="YYYY-MM-DD",
        help="Only export a single specific date instead of all un-exported dates.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Include rows already marked as exported (re-exports that date's tab).",
    )
    args = parser.parse_args()

    # ── Resolve which dates to process ───────────────────────────────────────
    if args.date:
        try:
            target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print(f"❌ Invalid date format: '{args.date}'. Use YYYY-MM-DD.")
            sys.exit(1)
        dates_to_process = [target_date]
    else:
        if args.force:
            dates_to_process = get_all_dates()
        else:
            dates_to_process = get_unexported_dates()

    if not dates_to_process:
        print("✅ Nothing to export — all contacts are already marked as exported.")
        return

    only_unexported = not args.force

    print(f"\n{'[DRY RUN] ' if args.dry_run else ''}Backfill summary")
    print(f"  Dates to process : {len(dates_to_process)}")
    print(f"  Force re-export  : {args.force}")
    print(f"  Dry run          : {args.dry_run}")
    print()

    # ── Collect row counts per date ───────────────────────────────────────────
    date_data: list[tuple[date, list[int], list[dict]]] = []
    for d in dates_to_process:
        ids, rows = _get_linkedin_contacts_for_date(d, only_unexported=only_unexported)
        date_data.append((d, ids, rows))
        label = f"linkedin_{d.strftime('%Y-%m-%d')}"
        print(f"  📅 {d}  →  {len(rows):4d} row(s)  →  tab: {label}")

    total_rows = sum(len(rows) for _, _, rows in date_data)
    print(f"\n  Total rows to export: {total_rows}")

    if args.dry_run:
        print("\n[DRY RUN] No changes made. Remove --dry-run to perform the actual export.")
        return

    if total_rows == 0:
        print("✅ No rows to export.")
        return

    # ── Connect to Google Sheets ──────────────────────────────────────────────
    sheet_id = os.getenv("LINKEDIN_CONTACTS_SHEET_ID")
    if not sheet_id:
        print("❌ LINKEDIN_CONTACTS_SHEET_ID is not set in .env. Aborting.")
        sys.exit(1)

    creds       = get_google_creds()
    client      = gspread.authorize(creds)
    spreadsheet = client.open_by_key(sheet_id)
    print(f"\n📊 Connected to spreadsheet: {spreadsheet.title}\n")

    # ── Export date by date ───────────────────────────────────────────────────
    exported_dates  = 0
    exported_rows   = 0
    failed_dates    = 0

    for target_date, contact_ids, contacts in date_data:
        if not contacts:
            print(f"  ⏭️  {target_date} — 0 rows, skipping.")
            continue

        tab_name = f"linkedin_{target_date.strftime('%Y-%m-%d')}"
        try:
            _write_contacts_to_sheet(spreadsheet, tab_name, contacts)
            _mark_contacts_exported(contact_ids)

            print(f"  ✅ {target_date} — exported {len(contacts)} row(s) to tab '{tab_name}'")
            exported_dates += 1
            exported_rows  += len(contacts)

        except Exception as exc:
            print(f"  ❌ {target_date} — FAILED: {exc}")
            logger.error(f"[Backfill] Failed to export {target_date}: {exc}", exc_info=True)
            failed_dates += 1

    # ── Final summary ─────────────────────────────────────────────────────────
    print(f"""
─────────────────────────────────────
Backfill complete
  Dates exported  : {exported_dates}
  Rows exported   : {exported_rows}
  Dates failed    : {failed_dates}
─────────────────────────────────────
""")

    if failed_dates:
        sys.exit(1)


if __name__ == "__main__":
    main()
