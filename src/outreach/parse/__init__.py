"""Stage 2 — extract structured fields from post text."""

from outreach.parse.emails import extract_emails
from outreach.parse.stage import run

__all__ = ["run", "extract_emails"]
