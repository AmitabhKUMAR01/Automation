from app.utils.logger import logger
import gspread
import os
from google.oauth2.service_account import Credentials
from datetime import date
from app.services.lead_service import get_todays_records

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


if __name__ == "__main__":
    test_connection()
