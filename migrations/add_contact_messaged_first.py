"""
Migration: add contact_messaged_first column to linkedin_search_contacts

Run this once:
    python migrations/add_contact_messaged_first.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.config.database import engine
from sqlalchemy import text

ADD_COLUMN_SQL = """
ALTER TABLE linkedin_search_contacts
    ADD COLUMN IF NOT EXISTS contact_messaged_first BOOLEAN NOT NULL DEFAULT FALSE;
"""

if __name__ == "__main__":
    with engine.connect() as conn:
        conn.execute(text(ADD_COLUMN_SQL))
        conn.commit()
    print("✅  Migration complete: contact_messaged_first added to linkedin_search_contacts.")
