"""Message templates for connection outreach."""

from __future__ import annotations

from outreach.config import NetworkConfig
from outreach.network.classify import first_name


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return ""


def compose_message(
    cfg: NetworkConfig,
    *,
    name: str,
    company: str | None,
    connected_days: int | None,
) -> str:
    recent = connected_days is not None and connected_days <= cfg.recent_days
    template = cfg.template_recent if recent else cfg.template_older
    org = company.strip() if company and company.strip() else "your organization"
    return template.format_map(_SafeDict(first_name=first_name(name), org=org, company=org)).strip()
