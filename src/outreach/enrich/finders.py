"""EmailFinder implementations: Hunter.io and Apollo."""

from __future__ import annotations

from typing import Any

from outreach.config import Settings
from outreach.http_util import HttpError, request_json
from outreach.logging import get_logger
from outreach.protocols import EmailFindResult, EmailFinder

log = get_logger("enrich.finder")


def _title_matches(title: str | None, targets: list[str]) -> bool:
    if not title:
        return False
    t = title.lower()
    return any(x.lower() in t for x in targets)


class HunterEmailFinder:
    name = "hunter"

    def __init__(self, api_key: str, *, min_confidence: float):
        self.api_key = api_key
        self.min_confidence = min_confidence

    def find(
        self,
        *,
        domain: str,
        company_name: str | None = None,
        target_titles: list[str] | None = None,
    ) -> EmailFindResult | None:
        targets = target_titles or []
        try:
            data = request_json(
                "GET",
                "https://api.hunter.io/v2/domain-search",
                params={
                    "domain": domain,
                    "api_key": self.api_key,
                    "limit": 10,
                    "department": "hr",
                },
                timeout=30.0,
            )
        except HttpError as exc:
            # department filter may fail on some plans — retry without it
            if exc.status_code and exc.status_code >= 400:
                data = request_json(
                    "GET",
                    "https://api.hunter.io/v2/domain-search",
                    params={"domain": domain, "api_key": self.api_key, "limit": 10},
                    timeout=30.0,
                )
            else:
                raise

        payload = (data or {}).get("data") or {}
        emails = payload.get("emails") or []
        best: EmailFindResult | None = None
        best_score = -1.0

        for row in emails:
            email = (row.get("value") or "").strip().lower()
            if not email or "@" not in email:
                continue
            conf = float(row.get("confidence") or 0) / 100.0
            position = row.get("position") or row.get("seniority") or ""
            # Prefer recruiting/HR titles when available
            score = conf
            if _title_matches(str(position), targets):
                score += 0.15
            if score < self.min_confidence:
                continue
            if score > best_score:
                best_score = score
                name_parts = [row.get("first_name") or "", row.get("last_name") or ""]
                best = EmailFindResult(
                    email=email,
                    confidence=min(conf, 1.0),
                    name=" ".join(p for p in name_parts if p).strip() or None,
                    title=str(position) if position else None,
                    domain=domain,
                    provider=self.name,
                )

        if best is None:
            log.info("hunter_no_confident_email", domain=domain)
        return best


class ApolloEmailFinder:
    name = "apollo"

    def __init__(self, api_key: str, *, min_confidence: float):
        self.api_key = api_key
        self.min_confidence = min_confidence

    def find(
        self,
        *,
        domain: str,
        company_name: str | None = None,
        target_titles: list[str] | None = None,
    ) -> EmailFindResult | None:
        targets = target_titles or ["recruiter", "talent", "human resources"]
        body: dict[str, Any] = {
            "api_key": self.api_key,
            "q_organization_domains": domain,
            "person_titles": targets,
            "page": 1,
            "per_page": 5,
        }
        data = request_json(
            "POST",
            "https://api.apollo.io/v1/mixed_people/search",
            headers={"Content-Type": "application/json", "Cache-Control": "no-cache"},
            json=body,
            timeout=40.0,
        )
        people = (data or {}).get("people") or []
        for person in people:
            email = (person.get("email") or "").strip().lower()
            # Apollo sometimes returns locked/null emails — never invent
            if not email or email.endswith("email_not_unlocked@") or "not_unlocked" in email:
                continue
            # Apollo doesn't always give confidence; require verified-ish status
            status = (person.get("email_status") or "").lower()
            conf = 0.85 if status in {"verified", "likely_to_engage"} else 0.6
            if conf < self.min_confidence:
                continue
            return EmailFindResult(
                email=email,
                confidence=conf,
                name=person.get("name"),
                title=person.get("title"),
                domain=domain,
                provider=self.name,
            )
        log.info("apollo_no_confident_email", domain=domain, company=company_name)
        return None


def get_email_finder(settings: Settings) -> EmailFinder:
    provider = settings.config.enrich.provider.lower().strip()
    min_conf = settings.config.enrich.min_confidence
    if provider == "hunter":
        if not settings.secrets.hunter_api_key:
            raise RuntimeError("HUNTER_API_KEY required when enrich.provider=hunter")
        return HunterEmailFinder(settings.secrets.hunter_api_key, min_confidence=min_conf)
    if provider == "apollo":
        if not settings.secrets.apollo_api_key:
            raise RuntimeError("APOLLO_API_KEY required when enrich.provider=apollo")
        return ApolloEmailFinder(settings.secrets.apollo_api_key, min_confidence=min_conf)
    raise RuntimeError(f"Unknown enrich.provider: {provider}")
