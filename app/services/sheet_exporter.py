from app.utils.logger import logger
import gspread
import os
from google.oauth2.service_account import Credentials
from datetime import date, datetime, timezone
from app.services.lead_service import get_todays_records
from app.config.database import SessionLocal
from app.models.linkedin_search_contact import LinkedinSearchContact
from sqlalchemy import func

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

## get google credentials
def get_google_creds() -> Credentials:
    private_key = os.getenv("GOOGLE_PRIVATE_KEY", "").replace("\\n", "\n")

    service_account_info = {
        "type": "service_account",
        "client_email": os.getenv("GOOGLE_SERVICE_ACCOUNT_EMAIL"),
        "private_key": private_key,
        "private_key_id": os.getenv("GOOGLE_PRIVATE_KEY_ID"),
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    
    return Credentials.from_service_account_info(service_account_info, scopes=SCOPES)

## test connection
def test_connection():
    creds = get_google_creds()

    client = gspread.authorize(creds)

    # open spreadsheet by name
    sheet = client.open(os.getenv("GOOGLE_SHEET_NAME"))

    logger.info("✅ Connection successful")
    logger.info("Spreadsheet title:", sheet.title)

## Main export function — called by the scheduler
def export_to_google_sheet():
    sheet_id = os.getenv("GOOGLE_SHEET_ID")

    if not sheet_id:
        logger.info("[Exporter] GOOGLE_SHEET_ID not set. Skipping.")
        return

    records = get_todays_records()
    if not records:
        logger.info(f"[Exporter] No records for today ({date.today()}). Skipping.")
        return

    # Auth from env
    creds  = get_google_creds()
    client = gspread.authorize(creds)

    # Open sheet, create a tab named today's date
    spreadsheet = client.open_by_key(sheet_id)
    tab_name    = date.today().strftime("%Y-%m-%d")

    try:
        worksheet = spreadsheet.worksheet(tab_name)
        worksheet.clear()
    except gspread.exceptions.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(
            title=tab_name, rows=len(records) + 5, cols=20
        )

    # Write header + rows
    headers = list(records[0].keys())
    rows    = [list(r.values()) for r in records]
    worksheet.update([headers] + rows)

    # Bold the header row
    worksheet.format("1:1", {"textFormat": {"bold": True}})

    logger.info(f"[Exporter] ✅ Exported {len(records)} records to tab '{tab_name}'.")


# ── LinkedIn Search Contacts Export ───────────────────────────────────────────

def _serialize_contact(c: LinkedinSearchContact) -> dict:
    """Convert a LinkedinSearchContact ORM row to a plain dict for sheet export."""
    return {
        "Name"               : c.name                or "",
        "Profile URL"        : c.profile_url         or "",
        "Position"           : c.position            or "",
        "Location"           : c.location            or "",
        "Source"             : c.source              or "",
        "Connection Sent"    : "Yes" if c.connection_sent    else "No",
        "Connection Sent At" : c.connection_sent_at.strftime("%Y-%m-%d %H:%M") if c.connection_sent_at else "",
        "Is Connected"       : "Yes" if c.is_connected       else "No",
        "Connected At"       : c.connected_at.strftime("%Y-%m-%d %H:%M")       if c.connected_at       else "",
        "Profile ID"         : str(c.profile_id)    if c.profile_id is not None else "",
        "Created At"         : c.created_at.strftime("%Y-%m-%d %H:%M")         if c.created_at         else "",
    }


def _get_linkedin_contacts_for_date(
    target_date: date,
    only_unexported: bool = True,
) -> tuple[list[int], list[dict]]:
    """
    Fetch LinkedinSearchContact rows for *target_date*.

    Args:
        target_date:      The calendar date to filter on (uses created_at).
        only_unexported:  If True, only returns rows where exported_at IS NULL.
                          If False, returns all rows for that date (force re-export).

    Returns:
        (contact_ids, serialized_rows)
    """
    db = SessionLocal()
    try:
        query = (
            db.query(LinkedinSearchContact)
            .filter(func.date(LinkedinSearchContact.created_at) == target_date)
            .order_by(LinkedinSearchContact.created_at.asc())
        )
        if only_unexported:
            query = query.filter(LinkedinSearchContact.exported_at.is_(None))

        contacts = query.all()
        ids  = [c.id for c in contacts]
        rows = [_serialize_contact(c) for c in contacts]
        return ids, rows
    finally:
        db.close()


def _mark_contacts_exported(contact_ids: list[int]) -> None:
    """
    Stamp exported_at = now(UTC) on every row in contact_ids.
    Called after a successful Google Sheets write.
    """
    if not contact_ids:
        return
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        (
            db.query(LinkedinSearchContact)
            .filter(LinkedinSearchContact.id.in_(contact_ids))
            .update({"exported_at": now}, synchronize_session=False)
        )
        db.commit()
        logger.info(f"[LinkedIn Exporter] ✅ Marked {len(contact_ids)} contacts as exported.")
    except Exception as exc:
        db.rollback()
        logger.error(f"[LinkedIn Exporter] ❌ Failed to mark contacts exported: {exc}")
    finally:
        db.close()


def _write_contacts_to_sheet(
    spreadsheet,
    tab_name: str,
    contacts: list[dict],
) -> None:
    """Open or create *tab_name* in *spreadsheet* and write *contacts*."""
    try:
        worksheet = spreadsheet.worksheet(tab_name)
        worksheet.clear()
    except gspread.exceptions.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(
            title=tab_name, rows=len(contacts) + 5, cols=len(contacts[0]) + 2
        )

    headers = list(contacts[0].keys())
    rows    = [list(c.values()) for c in contacts]
    worksheet.update([headers] + rows)
    worksheet.format("1:1", {"textFormat": {"bold": True}})


def export_linkedin_contacts_to_sheet() -> None:
    """
    Daily scheduler job — export today's un-exported LinkedIn contacts to Google Sheets,
    then stamp exported_at on each exported row.
    """
    sheet_id = os.getenv("LINKEDIN_CONTACTS_SHEET_ID")

    if not sheet_id:
        logger.warning("[LinkedIn Exporter] LINKEDIN_CONTACTS_SHEET_ID not set. Skipping.")
        return

    today = date.today()
    contact_ids, contacts = _get_linkedin_contacts_for_date(today, only_unexported=True)

    if not contacts:
        logger.info(f"[LinkedIn Exporter] No un-exported LinkedIn contacts for today ({today}). Skipping.")
        return

    # Auth + open sheet
    creds       = get_google_creds()
    client      = gspread.authorize(creds)
    spreadsheet = client.open_by_key(sheet_id)
    tab_name    = f"linkedin_{today.strftime('%Y-%m-%d')}"

    _write_contacts_to_sheet(spreadsheet, tab_name, contacts)

    # Mark as exported only after a successful sheet write
    _mark_contacts_exported(contact_ids)

    logger.info(f"[LinkedIn Exporter] ✅ Exported {len(contacts)} contacts to tab '{tab_name}'.")


if __name__ == "__main__":
    test_connection()

