"""Email extraction: plain + obfuscated forms common in hiring posts."""

from __future__ import annotations

import re

# name@domain.tld — trailing punctuation allowed via lookahead
_PLAIN = re.compile(
    r"(?<![A-Za-z0-9._%+\-])"
    r"([A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+)"
    r"(?![A-Za-z0-9_%+\-])"
)

# jane [at] brightlabs [dot] com  |  talent(at)novaapps(dot)io  |  HIRING AT MEGACORP DOT CO
_OBFUSCATED = re.compile(
    r"(?<![A-Za-z0-9._%+\-])"
    r"([A-Za-z0-9._%+\-]+)\s*"
    r"(?:"
    r"\[\s*at\s*\]|"
    r"\(\s*at\s*\)|"
    r"\{\s*at\s*\}|"
    r"(?<![A-Za-z])at(?![A-Za-z])|"
    r"@"
    r")\s*"
    r"([A-Za-z0-9\-]+)\s*"
    r"(?:"
    r"\[\s*dot\s*\]|"
    r"\(\s*dot\s*\)|"
    r"\{\s*dot\s*\}|"
    r"(?<![A-Za-z])dot(?![A-Za-z])|"
    r"\."
    r")\s*"
    r"([A-Za-z]{2,10})"
    r"(?![A-Za-z0-9_%+\-])",
    re.IGNORECASE,
)

_MAIL_TO_HINT = re.compile(
    r"(?:mail|email|send)\s+(?:your\s+)?(?:cv|resume|application)?\s*"
    r"(?:to|at)\s+([A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+)",
    re.IGNORECASE,
)


def normalize_email(email: str) -> str:
    return email.strip().lower().rstrip(".,;:)>\"'")


def _looks_valid(email: str) -> bool:
    if "@" not in email or " " in email:
        return False
    local, _, domain = email.partition("@")
    if not local or "." not in domain:
        return False
    tld = domain.rsplit(".", 1)[-1]
    return 2 <= len(tld) <= 24 and tld.isalpha()


def extract_emails(text: str) -> list[str]:
    """Return unique emails found in text, preferring hinted contacts first."""
    found: list[str] = []

    for match in _MAIL_TO_HINT.finditer(text):
        found.append(normalize_email(match.group(1)))

    for match in _PLAIN.finditer(text):
        found.append(normalize_email(match.group(1)))

    for match in _OBFUSCATED.finditer(text):
        local, domain, tld = match.group(1), match.group(2), match.group(3)
        found.append(normalize_email(f"{local}@{domain}.{tld}"))

    seen: set[str] = set()
    out: list[str] = []
    for email in found:
        if email in seen or not _looks_valid(email):
            continue
        seen.add(email)
        out.append(email)
    return out
