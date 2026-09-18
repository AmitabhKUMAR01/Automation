"""Company domain resolution (best-effort, no guessed emails)."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from outreach.http_util import HttpError, request_json
from outreach.logging import get_logger

log = get_logger("enrich.domain")

_DOMAIN_RE = re.compile(
    r"(?:https?://)?(?:www\.)?([a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+)",
    re.IGNORECASE,
)


def domain_from_text(text: str) -> str | None:
    """Extract a plausible company domain from post URLs / mentions."""
    skip = {
        "linkedin.com",
        "lnkd.in",
        "bit.ly",
        "google.com",
        "forms.gle",
        "docs.google.com",
        "youtube.com",
        "twitter.com",
        "x.com",
        "facebook.com",
        "instagram.com",
        "github.com",
        "notion.so",
        "medium.com",
    }
    for match in _DOMAIN_RE.finditer(text or ""):
        host = match.group(1).lower().rstrip(".")
        if any(host == s or host.endswith("." + s) for s in skip):
            continue
        if host.count(".") >= 1:
            return host
    return None


def domain_from_clearbit(company_name: str) -> str | None:
    """Free Clearbit autocomplete suggest — returns domain if confident."""
    name = (company_name or "").strip()
    if len(name) < 2:
        return None
    try:
        data = request_json(
            "GET",
            "https://autocomplete.clearbit.com/v1/companies/suggest",
            params={"query": name},
            timeout=20.0,
        )
    except (HttpError, Exception) as exc:
        log.warning("clearbit_failed", company=name, error=str(exc))
        return None
    if not isinstance(data, list) or not data:
        return None
    top = data[0]
    domain = (top.get("domain") or "").strip().lower()
    return domain or None


def resolve_company_domain(*, company_name: str | None, post_text: str) -> str | None:
    from_text = domain_from_text(post_text)
    if from_text:
        return from_text
    if company_name:
        return domain_from_clearbit(company_name)
    return None


def normalize_domain(value: str) -> str:
    value = value.strip().lower()
    if "://" in value:
        value = urlparse(value).netloc or value
    return value.removeprefix("www.")
