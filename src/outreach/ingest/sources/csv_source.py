"""CSV ingest source for local fixtures / manual posts."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from outreach.config import Settings
from outreach.protocols import RawPost


class CsvSource:
    name = "csv"

    def __init__(self, settings: Settings, path: Path | None = None):
        self.settings = settings
        if path is not None:
            self.path = path
        else:
            self.path = settings.resolve_path(settings.config.ingest.csv_path)

    def fetch(self) -> list[RawPost]:
        if not self.path.exists():
            raise FileNotFoundError(f"CSV source not found: {self.path}")

        posts: list[RawPost] = []
        with self.path.open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                url = (row.get("url") or "").strip()
                text = (row.get("raw_text") or row.get("text") or "").strip()
                if not url or not text:
                    continue
                posted_raw = (row.get("posted_at") or "").strip()
                posted_at = None
                if posted_raw:
                    try:
                        posted_at = datetime.fromisoformat(posted_raw.replace("Z", "+00:00"))
                        if posted_at.tzinfo is None:
                            posted_at = posted_at.replace(tzinfo=timezone.utc)
                    except ValueError:
                        posted_at = None
                posts.append(
                    RawPost(
                        url=url,
                        raw_text=text,
                        author_name=(row.get("author_name") or None),
                        author_profile_url=(row.get("author_profile_url") or None),
                        posted_at=posted_at,
                        source="csv",
                    )
                )
                if len(posts) >= self.settings.config.ingest.max_posts_per_run:
                    break
        return posts
