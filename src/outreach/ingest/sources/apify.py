"""Apify LinkedIn post search source via REST API."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from outreach.config import Settings
from outreach.http_util import request_json
from outreach.logging import get_logger
from outreach.protocols import RawPost

log = get_logger("ingest.apify")

APIFY_BASE = "https://api.apify.com/v2"


def _lookback_to_date_filter(days: int) -> str:
    if days <= 1:
        return "past-24h"
    if days <= 7:
        return "past-week"
    if days <= 30:
        return "past-month"
    return ""


def build_keyword(search_terms: list[str], target_roles: list[str]) -> str:
    """Build a LinkedIn-friendly keyword (keep it short — complex OR trees return little)."""
    hiring = [t.strip() for t in search_terms if t.strip()][:3]
    roles = [r.strip() for r in target_roles if r.strip()][:4]
    # Prefer: hiring ("full stack developer" OR "react developer" OR ...)
    role_part = " OR ".join(f'"{r}"' for r in roles) if roles else ""
    hire_part = hiring[0] if hiring else "hiring"
    if role_part:
        return f'{hire_part} ({role_part})'
    return hire_part or "hiring"


def _dig(item: dict[str, Any], *paths: str) -> Any:
    for path in paths:
        cur: Any = item
        ok = True
        for key in path.split("."):
            if isinstance(cur, dict) and key in cur:
                cur = cur[key]
            else:
                ok = False
                break
        if ok and cur not in (None, ""):
            return cur
    return None


def _parse_posted_at(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        # ms or seconds
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            return None
    return None


def item_to_raw_post(item: dict[str, Any], *, source: str = "apify") -> RawPost | None:
    """Normalize heterogeneous Apify actor item shapes into RawPost."""
    # Some actors nest the post under "post" / "data"
    if "post" in item and isinstance(item["post"], dict):
        item = {**item, **item["post"]}

    url = _dig(
        item,
        "url",
        "postUrl",
        "post_url",
        "linkedinUrl",
        "link",
        "postLink",
        "post_link",
        "activityUrl",
        "shareUrl",
        "post.link",
    )
    text = _dig(
        item,
        "text",
        "commentary",
        "postText",
        "post_text",
        "content",
        "description",
        "commentaryText",
        "resharedPostText",
        "post.text",
    )
    if not url and item.get("urn"):
        urn = str(item["urn"])
        url = f"https://www.linkedin.com/feed/update/{urn}"
    if not text and isinstance(item.get("content"), dict):
        text = item["content"].get("text") or item["content"].get("commentary")

    if not url or not text:
        log.debug(
            "apify_item_skipped",
            keys=sorted(item.keys()),
            has_url=bool(url),
            has_text=bool(text),
        )
        return None

    author = _dig(
        item,
        "authorName",
        "author_name",
        "author.name",
        "author.fullName",
        "fullName",
        "name",
    )
    author_url = _dig(
        item,
        "authorProfileUrl",
        "author_profile_url",
        "authorUrl",
        "author.url",
        "author.profileUrl",
        "profileUrl",
    )
    posted_at = _parse_posted_at(
        _dig(item, "postedAt", "posted_at", "publishedAt", "createdAt", "postedDate", "timestamp")
    )

    return RawPost(
        url=str(url).strip(),
        raw_text=str(text).strip(),
        author_name=str(author).strip() if author else None,
        author_profile_url=str(author_url).strip() if author_url else None,
        posted_at=posted_at,
        source=source,
        extra={"apify_item_keys": sorted(item.keys())},
    )


class ApifyLinkedInSource:
    name = "apify"

    def __init__(self, settings: Settings):
        self.settings = settings

    def queries(self) -> list[str]:
        cfg = self.settings.config.ingest
        custom = [q.strip() for q in cfg.post_search_queries if q.strip()]
        return custom or [build_keyword(cfg.search_terms, cfg.target_roles)]

    def build_input(self, keyword: str | None = None) -> dict[str, Any]:
        cfg = self.settings.config.ingest
        per_query = max(1, min(cfg.post_search_per_query, 50))
        payload: dict[str, Any] = {
            "keyword": keyword or self.queries()[0],
            "sort_type": "date_posted",
            "date_filter": _lookback_to_date_filter(cfg.lookback_days),
            "total_posts": per_query,
            "limit": per_query,
        }
        return payload

    def _run_query(self, token: str, keyword: str) -> list[Any]:
        actor_id = self.settings.secrets.apify_actor_id.replace("/", "~")
        run_input = self.build_input(keyword)
        log.info("apify_fetch_start", actor=self.settings.secrets.apify_actor_id, input=run_input)
        # Sync endpoint returns dataset items directly (up to ~300s).
        # Auth via Bearer header — never put the token in the URL (avoids log leaks).
        url = f"{APIFY_BASE}/acts/{actor_id}/run-sync-get-dataset-items"
        items = request_json(
            "POST",
            url,
            headers={"Authorization": f"Bearer {token}"},
            params={"format": "json", "clean": "true"},
            json=run_input,
            timeout=320.0,
        )
        if not isinstance(items, list):
            raise RuntimeError(f"Unexpected Apify response type: {type(items)}")
        return items

    def fetch(self) -> list[RawPost]:
        token = self.settings.secrets.apify_api_token
        if not token:
            raise RuntimeError("APIFY_API_TOKEN (or APIFY_API_KEY) is required for ingest.source=apify")

        from outreach.ingest.known import load_known_inventory

        cfg = self.settings.config.ingest
        known = load_known_inventory()
        cutoff = datetime.now(timezone.utc) - timedelta(days=cfg.lookback_days)
        collected: dict[str, RawPost] = {}
        for keyword in self.queries():
            if len(collected) >= cfg.post_search_max_total:
                break
            try:
                items = self._run_query(token, keyword)
            except Exception as exc:  # noqa: BLE001
                # One bad query should not lose the others' results
                log.warning("apify_query_failed", keyword=keyword, error=str(exc))
                continue
            fresh = 0
            for item in items:
                if not isinstance(item, dict):
                    continue
                raw = item_to_raw_post(item)
                if raw is None or raw.url in collected or raw.url in known.urls:
                    continue
                if raw.posted_at and raw.posted_at < cutoff:
                    continue
                collected[raw.url] = raw
                fresh += 1
                if len(collected) >= cfg.post_search_max_total:
                    break
            log.info("apify_query_done", keyword=keyword, fetched=len(items), new=fresh)

        posts = list(collected.values())
        log.info(
            "apify_fetch_done",
            queries=len(self.queries()),
            kept=len(posts),
            new_emails=sum(1 for p in posts if known.new_emails(p.raw_text)),
        )
        return posts
