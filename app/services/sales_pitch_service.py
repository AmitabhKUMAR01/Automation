from __future__ import annotations
from app.utils.logger import logger
import os
import time
import re
from typing import Optional
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception
from app.models.business_client import Business_Client
from app.models.linkedin_contact import LinkedinContact
from app.models.linkedin_search_contact import LinkedinSearchContact
from app.models.website_audit import WebsiteAudit
from app.core.prompts import SYSTEM_PROMPT, EMAIL_PROMPT_TEMPLATE, LINKEDIN_PROMPT_TEMPLATE
from app.core.llm_provider import get_llm
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from app.config.database import SessionLocal
from app.models.profile_setting import ProfileSetting


# ── Contact ranking ───────────────────────────────────────────────────────────

_TITLE_TIERS: list[tuple[int, list[str]]] = [
    (100, ["owner", "founder", "ceo", "chief executive", "managing director", "md", "president"]),
    (70,  ["director", "vp ", "vice president", "cmo", "cto", "coo", "cfo", "head of", "manager"]),
    (40,  ["executive", "lead", "coordinator", "officer", "principal", "supervisor"]),
]


def _rank_contact(contact: LinkedinContact) -> int:
    """Score a LinkedIn contact by decision-maker seniority.
    Connected contacts get a +20 bonus (can DM directly).
    """
    title = (contact.job_title or "").lower()
    score = 10  # default tier-4
    for tier_score, keywords in _TITLE_TIERS:
        if any(kw in title for kw in keywords):
            score = tier_score
            break
    if contact.is_connected:
        score += 20
    return score


def rank_contacts(contacts: list[LinkedinContact]) -> list[LinkedinContact]:
    """Return contacts sorted highest-priority first."""
    return sorted(contacts, key=_rank_contact, reverse=True)


def _first_name(full_name: str | None) -> str:
    if not full_name:
        return "there"
    return full_name.strip().split()[0]


# ── Retry / transient-error helpers ──────────────────────────────────────────

def _should_retry(exception: Exception) -> bool:
    msg = str(exception).lower()
    transient = [
        "429", "resource_exhausted", "quota", "rate limit", "ratelimit",
        "tpm", "rpm", "503", "overloaded", "unavailable", "timeout", "too many requests"
    ]
    is_transient = any(ind in msg for ind in transient)
    if is_transient:
        logger.warning(f"[PITCH] ⚠️  Transient LLM rate-limit or API error ({exception}). Retrying with backoff...")
    return is_transient


# ── Rule-based fallbacks ──────────────────────────────────────────────────────

def _rule_based_pitch(
    client: Optional[Business_Client],
    score_data: dict,
    contact_email: str | None = None,
    sender_name: str = "Team",
) -> dict:
    name       = client.name     if client else "there"
    category   = client.category if client else "your business"
    city       = client.city     if client else "your area"
    grade      = score_data.get("grade", "?")
    total      = score_data.get("total_score", 0)
    issues     = score_data.get("issues_found", [])
    top_issues = issues[:3]

    subject = f"Quick win for {name}'s website — your site scored {total}/100"
    hook = (
        f"Hi {name} team, I just ran a free audit of your website and found "
        f"some quick wins that could directly impact your {category.lower()} "
        f"bookings in {city}."
    )
    pain_block = (
        "Here's what we found:\n" + "\n".join(f"  • {i}" for i in top_issues)
        if top_issues
        else "Your site is performing well, but there's always room for improvement."
    )
    if grade in ("D", "F"):
        urgency = (
            f"With a score of {total}/100 (Grade {grade}), your competitors are "
            "likely outranking you right now. These are fixable issues — let's talk."
        )
    elif grade in ("B", "C"):
        urgency = (
            f"Your site scored {total}/100 (Grade {grade}) — solid foundation, "
            "but a few targeted fixes could push you into the top tier."
        )
    else:
        urgency = (
            f"Your site scored {total}/100 (Grade {grade}) — impressive! "
            "We have a few refinements that could make it even better."
        )

    body = (
        f"{hook}\n\n{pain_block}\n\n{urgency}\n\n"
        f"We specialise in fast, measurable website improvements for businesses "
        f"like yours in {city}. No long contracts — just results.\n\n"
        f"Would you be open to a 15-minute call this week?\n\nBest,\n{sender_name}"
    )
    return {
        "pitch_subject":  subject,
        "pitch_hook":     hook,
        "pitch_body":     body,
        "key_pain_points": top_issues,
        "generated_by":   "rule_engine",
        "pitch_channel":  "email" if contact_email else "generic",
    }


def _rule_based_linkedin_pitch(
    client: Optional[Business_Client],
    contact: LinkedinContact,
    score_data: dict,
) -> dict:
    first     = _first_name(contact.name)
    title     = contact.job_title or "your role"
    name      = client.name if client else "your company"
    total     = score_data.get("total_score", 0)
    grade     = score_data.get("grade", "?")
    issues    = score_data.get("issues_found", [])
    top_issue = issues[0] if issues else "a few quick wins on your website"

    hook = f"Hi {first}, I came across {name} and ran a quick website audit."
    body = (
        f"{hook}\n\n"
        f"As someone in {title}, you'd likely care that the site scored {total}/100 "
        f"(Grade {grade}) — the biggest flag was: {top_issue}.\n\n"
        "These are the kind of things that quietly cost leads every month. "
        "I've helped similar businesses fix them quickly with no long contracts.\n\n"
        "Would it be worth a quick chat?"
    )
    return {
        "pitch_subject":  None,
        "pitch_hook":     hook,
        "pitch_body":     body,
        "key_pain_points": issues[:3],
        "generated_by":   "rule_engine",
        "pitch_channel":  "linkedin",
    }


# ── LLM pitch ─────────────────────────────────────────────────────────────────

@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=3, max=30),
    retry=retry_if_exception(_should_retry),
    reraise=True,
)
def _llm_pitch(
    client: Optional[Business_Client],
    audit: WebsiteAudit,
    score_data: dict,
    provider: str,
    channel: str = "email",
    contact: Optional[LinkedinContact] = None,
    contact_email: str | None = None,
) -> dict:
    name      = client.name     if client else "the business"
    category  = client.category if client else "local business"
    city      = client.city     if client else "the city"
    url       = audit.url       or "their website"
    grade     = score_data.get("grade", "?")
    total     = score_data.get("total_score", 0)
    issues    = score_data.get("issues_found", [])
    breakdown = score_data.get("score_breakdown", {})

    top_issues_text = "\n".join(f"- {i}" for i in issues[:5]) if issues else "- No major issues"

    # Select template and build invoke payload
    if channel == "linkedin" and contact:
        template = LINKEDIN_PROMPT_TEMPLATE
        invoke_vars = {
            "contact_name":  contact.name or "there",
            "contact_title": contact.job_title or "your role",
            "name": name, "category": category, "city": city, "url": url,
            "total": total, "grade": grade,
            "perf":    breakdown.get("performance", 0),
            "seo":     breakdown.get("seo", 0),
            "a11y":    breakdown.get("accessibility", 0),
            "sec":     breakdown.get("security", 0),
            "content": breakdown.get("content", 0),
            "issues":  top_issues_text,
        }
    else:
        template = EMAIL_PROMPT_TEMPLATE
        invoke_vars = {
            "contact_name":  (contact.name if contact else None) or name + " team",
            "contact_email": contact_email or "",
            "name": name, "category": category, "city": city, "url": url,
            "total": total, "grade": grade,
            "perf":    breakdown.get("performance", 0),
            "seo":     breakdown.get("seo", 0),
            "a11y":    breakdown.get("accessibility", 0),
            "sec":     breakdown.get("security", 0),
            "content": breakdown.get("content", 0),
            "issues":  top_issues_text,
        }

    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", template),
    ])

    llm    = get_llm(provider=provider)
    parser = JsonOutputParser()
    chain  = prompt | llm | parser

    api_delay = float(os.getenv("LLM_API_DELAY_SEC", "1.0"))
    if api_delay > 0:
        time.sleep(api_delay)

    result = chain.invoke(invoke_vars)

    return {
        "pitch_subject":  result.get("pitch_subject"),
        "pitch_hook":     result.get("pitch_hook"),
        "pitch_body":     result.get("pitch_body"),
        "key_pain_points": result.get("key_pain_points", issues[:3]),
        "generated_by":   provider,
        "pitch_channel":  channel,
    }


# ── Grade gate & Provider helpers ──────────────────────────────────────────────

_GRADE_ORDER = ["F", "D", "C", "B", "A"]


def _has_provider_key(provider: str) -> bool:
    provider = provider.strip().lower()
    if provider == "gemini":
        return bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))
    elif provider == "groq":
        return bool(os.getenv("GROQ_API_KEY"))
    elif provider == "openai":
        return bool(os.getenv("OPENAI_API_KEY"))
    elif provider == "claude":
        return bool(os.getenv("ANTHROPIC_API_KEY"))
    return False


def _grade_qualifies(grade: str, min_grade: str) -> bool:
    min_g = min_grade.upper()
    if min_g in ("ALL", "*"):
        return True
    try:
        # min_grade="A" or "ALL" qualifies all grades (F, D, C, B, A)
        return _GRADE_ORDER.index(grade.upper()) <= _GRADE_ORDER.index(min_g)
    except ValueError:
        return True


# ── Public entry point ────────────────────────────────────────────────────────

def generate_sales_pitch(
    client: Optional[Business_Client],
    audit: WebsiteAudit,
    score_data: dict,
    linkedin_contact: Optional[LinkedinContact] = None,
    contact_email: str | None = None,
    profile_id: Optional[int] = None,
) -> dict:
    # Fetch sender name dynamically from the database using profile_id or the first active profile
    sender_name = "Team"
    db = SessionLocal()
    try:
        if profile_id:
            profile = db.query(ProfileSetting).filter(ProfileSetting.id == profile_id).first()
        else:
            profile = db.query(ProfileSetting).filter(ProfileSetting.is_active == True).first()
        if profile and profile.name:
            sender_name = profile.name
    except Exception as e:
        logger.warning(f"[PITCH] Failed to query ProfileSetting name: {e}")
    finally:
        db.close()

    if not client or client.scrape_source != "google_maps":
        greeting = "Hi,"
        if linkedin_contact and linkedin_contact.name:
            greeting = f"Hi {_first_name(linkedin_contact.name)},"
        elif client and client.name:
            greeting = f"Hi {client.name},"

        body = (
            f"{greeting}\n\n"
            "I hope you’re doing well.\n\n"
            "We are a full-stack development team specializing\n\n"
            "• AI Development (Agentic AI, Chatbots, Automation, RAG, LLM Integrations)\n"
            "• Web Development (Websites, SaaS Platforms, APIs, Cloud Systems)\n"
            "• App Development (iOS, Android, React Native, AI-Powered Apps)\n"
            "• UI/UX Design\n"
            "• SEO, SMO SEM & Google Ads\n\n"
            "We help businesses build and scale reliable digital products. If you’re planning a project or improving an existing system, we’d be happy to connect.\n\n"
            "Looking forward to hearing from you.\n\n"
            "Best regards,\n"
            f"{sender_name}"
        )
        return {
            "pitch_subject": "Collaboration / Development Services",
            "pitch_hook": "I hope you’re doing well.",
            "pitch_body": body,
            "key_pain_points": [],
            "generated_by": "static_template",
            "pitch_channel": "linkedin" if linkedin_contact else ("email" if contact_email else "generic"),
        }

    primary_provider = os.getenv("LLM_PROVIDER", "groq").strip().lower()
    min_grade        = os.getenv("PITCH_LLM_MIN_GRADE", "A").strip().upper()
    lead_grade       = score_data.get("grade", "A")

    qualifies = _grade_qualifies(lead_grade, min_grade)

    # Determine channel
    if linkedin_contact:
        channel = "linkedin"
    elif contact_email:
        channel = "email"
    else:
        channel = "generic"

    if not qualifies:
        logger.info(
            f"[PITCH] ℹ️  Grade {lead_grade} does not qualify for LLM (threshold {min_grade}) — "
            f"rule-based {channel} pitch."
        )
        if channel == "linkedin":
            return _rule_based_linkedin_pitch(client, linkedin_contact, score_data)
        return _rule_based_pitch(client, score_data, contact_email, sender_name=sender_name)

    # Build ordered list of candidate LLM providers: primary provider first, followed by fallbacks
    candidate_providers = [primary_provider]
    for p in ["groq", "gemini", "openai", "claude"]:
        if p != primary_provider and _has_provider_key(p):
            candidate_providers.append(p)

    for prov in candidate_providers:
        if not _has_provider_key(prov):
            continue
        try:
            return _llm_pitch(
                client, audit, score_data, prov,
                channel=channel,
                contact=linkedin_contact,
                contact_email=contact_email,
            )
        except Exception as exc:
            logger.warning(f"[PITCH] ⚠️  LLM provider '{prov}' failed ({exc}). Retrying with next available provider if any...")

    logger.warning(f"[PITCH] ⚠️  All LLM providers failed or unconfigured — falling back to rule-based {channel} pitch.")
    if channel == "linkedin":
        return _rule_based_linkedin_pitch(client, linkedin_contact, score_data)
    return _rule_based_pitch(client, score_data, contact_email, sender_name=sender_name)
