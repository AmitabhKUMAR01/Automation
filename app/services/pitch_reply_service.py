from __future__ import annotations
from app.utils.logger import logger
import os
import time
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session

from app.config.database import SessionLocal
from app.models.sales_pitch import SalesPitch
from app.models.linkedin_contact import LinkedinContact
from app.models.linkedin_search_contact import LinkedinSearchContact
import app.models.lead_score        # noqa: F401  — registers lead_scores table
import app.models.business_client   # noqa: F401  — registers business_clients table
import app.models.linkedin_contact  # noqa: F401  — registers linkedin_contacts table
import app.models.linkedin_search_contact  # noqa: F401  — registers linkedin_search_contacts table
from app.scraper.linkedin_reply_checker import check_reply_for_contact
from app.scraper.linkedin_inbox_scanner import scan_inbox
from app.scraper.linkedin_finder import LinkedInSessionExpiredError
from app.services.notification_service import notify_reply_received
from app.models.business_client import Business_Client
from app.models.client_chat_message import ClientChatMessage
from app.core.prompts import SUGGESTED_REPLY_PROMPT_TEMPLATE, SYSTEM_PROMPT
from app.core.llm_provider import get_llm
from app.services.sales_pitch_service import _has_provider_key
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser


def generate_suggested_reply(
    contact_name: str,
    company_name: str,
    pitch_context: str,
    messages: list[dict],
) -> str | None:
    """
    Generate an LLM suggested follow-up response based on full chat history.
    """
    if not messages:
        return None

    # Format full chronological chat history
    formatted_chat_lines = []
    for msg in messages:
        sender_label = "You" if msg.get("is_self") else (msg.get("sender") or "Them")
        body = msg.get("body") or ""
        formatted_chat_lines.append(f"{sender_label}: {body}")

    chat_history_text = "\n".join(formatted_chat_lines)

    primary_provider = os.getenv("LLM_PROVIDER", "groq").strip().lower()
    candidate_providers = [primary_provider]
    for p in ["groq", "gemini", "openai", "claude"]:
        if p != primary_provider and _has_provider_key(p):
            candidate_providers.append(p)

    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", SUGGESTED_REPLY_PROMPT_TEMPLATE),
    ])
    parser = JsonOutputParser()

    invoke_vars = {
        "contact_name": contact_name or "there",
        "company_name": company_name or "your company",
        "pitch_context": (pitch_context or "")[:500],
        "chat_history_text": chat_history_text,
    }

    for prov in candidate_providers:
        if not _has_provider_key(prov):
            continue
        try:
            llm = get_llm(provider=prov)
            chain = prompt | llm | parser
            result = chain.invoke(invoke_vars)
            suggested = result.get("suggested_reply")
            if suggested:
                return suggested
        except Exception as exc:
            logger.warning(f"[REPLY CHECK] ⚠️ LLM provider '{prov}' failed to generate suggested reply: {exc}")

    return None


def _sync_chat_history(
    pitch: SalesPitch,
    contact: object,
    profile_id: int,
    messages: list[dict],
    suggested_reply: str | None,
    telegram_chat_id: str | None,
    db: Session,
) -> None:
    """
    Persist thread messages into client_chat_messages table for this pitch & profile.
    """
    if not messages:
        return

    contact_id = getattr(pitch, "linkedin_contact_id", None)
    search_contact_id = getattr(pitch, "linkedin_search_contact_id", None)

    for idx, msg in enumerate(messages):
        body = msg.get("body") or ""
        sender = msg.get("sender") or ("You" if msg.get("is_self") else "Them")
        is_self = bool(msg.get("is_self"))

        # Check if message already recorded for this pitch/contact
        existing = db.query(ClientChatMessage).filter(
            ClientChatMessage.sales_pitch_id == pitch.id,
            ClientChatMessage.sender == sender,
            ClientChatMessage.message_body == body,
        ).first()

        if existing:
            if suggested_reply and not existing.suggested_reply:
                existing.suggested_reply = suggested_reply
            if telegram_chat_id and not existing.telegram_chat_id:
                existing.telegram_chat_id = telegram_chat_id
        else:
            is_last = (idx == len(messages) - 1)
            record = ClientChatMessage(
                profile_id=profile_id,
                sales_pitch_id=pitch.id,
                linkedin_contact_id=contact_id,
                linkedin_search_contact_id=search_contact_id,
                sender=sender,
                message_body=body,
                is_self=is_self,
                suggested_reply=suggested_reply if is_last and not is_self else None,
                telegram_chat_id=telegram_chat_id if is_last and not is_self else None,
                status="received" if not is_self else "sent",
            )
            db.add(record)

    db.commit()


def _cfg() -> dict:
    return {
        # Only re-check pitches whose reply_checked_at is older than this many hours
        "recheck_hours"    : int(os.getenv("REPLY_RECHECK_HOURS",      "4")),
        # Maximum pitches to check per run (avoid long Playwright sessions)
        "max_per_run"      : int(os.getenv("REPLY_MAX_PER_RUN",        "20")),
        # Delay between each contact check (seconds) — reduces LinkedIn bot risk
        "delay_between"    : float(os.getenv("REPLY_CHECK_DELAY_SEC",  "10.0")),
        # Maximum scrolls on the inbox page
        "inbox_max_scroll" : int(os.getenv("INBOX_MAX_SCROLL",         "3")),
    }


# ── DB Helpers ─────────────────────────────────────────────────────────────────

def _mark_replied(pitch: SalesPitch, reply_text: str, db: Session) -> None:
    now = datetime.now(timezone.utc)
    pitch.reply_received    = True
    pitch.reply_text        = reply_text[:2000]
    pitch.replied_at        = now
    pitch.reply_checked_at  = now
    db.commit()


def _mark_checked_no_reply(pitch: SalesPitch, db: Session) -> None:
    pitch.reply_checked_at = datetime.now(timezone.utc)
    db.commit()


def _fetch_checkable_pitches(db: Session, recheck_hours: int, max_per_run: int) -> list[SalesPitch]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=recheck_hours)

    return (
        db.query(SalesPitch)
        .filter(
            SalesPitch.pitch_channel    == "linkedin",
            SalesPitch.delivery_status  == "sent",
            SalesPitch.reply_received   == False,          # noqa: E712
            (
                SalesPitch.reply_checked_at.is_(None)
                | (SalesPitch.reply_checked_at < cutoff)
            ),
        )
        .order_by(SalesPitch.delivered_at.asc())
        .limit(max_per_run)
        .all()
    )


# ── Contact resolution helper ─────────────────────────────────────────────────

def _resolve_contact(pitch: SalesPitch, db: Session) -> tuple:
    """
    Resolve the linked contact for a pitch.
    Returns (contact, contact_type) or (None, None) if not found.
    """
    if pitch.linkedin_search_contact_id:
        contact = db.query(LinkedinSearchContact).filter(
            LinkedinSearchContact.id == pitch.linkedin_search_contact_id
        ).first()
        return contact, "LinkedinSearchContact"
    elif pitch.linkedin_contact_id:
        contact = db.query(LinkedinContact).filter(
            LinkedinContact.id == pitch.linkedin_contact_id
        ).first()
        return contact, "LinkedinContact"
    return None, None


def _extract_slug_from_url(profile_url: str) -> str | None:
    """Extract the /in/<slug> part from a profile URL, lowercased."""
    if not profile_url:
        return None
    import re
    match = re.search(r"/in/([^/?#]+)", profile_url)
    if match:
        return match.group(1).lower().rstrip("/")
    return None


# ── Phase 1: Inbox Scan ───────────────────────────────────────────────────────

def _run_inbox_scan_phase(
    db: Session,
    pitches: list[SalesPitch],
    profile_id: int,
    cfg: dict,
) -> list[SalesPitch] | None:
    """
    Phase 1: Scan the LinkedIn inbox for unread conversations and
    cross-reference against the given pitches.

    Returns a shortlist of pitches that likely have replies,
    or None if the inbox scan failed (caller should fall back).
    """
    logger.info("[REPLY CHECK] 📬 Phase 1: Scanning LinkedIn inbox for unread conversations…")

    try:
        inbox_result = scan_inbox(
            db=db,
            profile_id=profile_id,
            max_scroll=cfg["inbox_max_scroll"],
        )
    except LinkedInSessionExpiredError:
        raise  # let it propagate to stop the entire job
    except Exception as exc:
        logger.info(f"[REPLY CHECK] ⚠️  Inbox scan failed: {exc} — falling back to per-contact check.")
        return None

    if not inbox_result["success"]:
        logger.info(f"[REPLY CHECK] ⚠️  Inbox scan unsuccessful: {inbox_result.get('error')} — falling back.")
        return None

    unread = inbox_result["unread_conversations"]
    if not unread:
        logger.info("[REPLY CHECK] ℹ️  Phase 1: No unread conversations found — no replies to check.")
        # Mark all pitches as checked (no reply) to update their timestamps
        for pitch in pitches:
            _mark_checked_no_reply(pitch, db)
        return []  # empty shortlist — nothing to deep-check

    # Build lookup sets from unread conversations
    unread_slugs = {c["profile_slug"] for c in unread if c.get("profile_slug")}
    unread_names = {c["name"].strip().lower() for c in unread if c.get("name")}

    logger.info(
        f"[REPLY CHECK] 📊 Phase 1 results: {len(unread)} unread conversation(s) | "
        f"slugs={unread_slugs} | names={unread_names}"
    )

    # Cross-reference: find pitches whose contact matches an unread conversation
    shortlist: list[SalesPitch] = []
    for pitch in pitches:
        contact, _ = _resolve_contact(pitch, db)
        if not contact:
            continue

        matched = False
        # Match by profile slug
        contact_slug = _extract_slug_from_url(getattr(contact, "profile_url", None))
        if contact_slug and contact_slug in unread_slugs:
            matched = True

        # Match by name (fallback) — multi-tier to handle abbreviations and suffixes
        if not matched and hasattr(contact, "name") and contact.name:
            contact_name_lower = contact.name.strip().lower()

            import re as _re
            def _strip_punct(s: str) -> str:
                return _re.sub(r"[^\w\s]", "", s).strip()

            contact_clean = _strip_punct(contact_name_lower)

            for inbox_name in unread_names:
                inbox_clean = _strip_punct(inbox_name)

                # Tier 1: direct containment (original logic)
                if contact_name_lower in inbox_name or inbox_name in contact_name_lower:
                    matched = True
                    break

                # Tier 2: punctuation-stripped containment
                # "Aravind S." -> "aravind s" which IS in "aravind subramaniyan as"
                if contact_clean and (
                    contact_clean in inbox_clean or inbox_clean in contact_clean
                ):
                    matched = True
                    break

                # Tier 3: first-name match for abbreviated DB names like "Aravind S."
                # Guard: first name >= 4 chars, last token looks abbreviated (1 letter or ends with ".")
                tokens_clean = contact_clean.split()
                first_token = tokens_clean[0] if tokens_clean else ""
                tokens_orig = contact_name_lower.split()
                last_token = tokens_orig[-1] if tokens_orig else ""
                is_abbreviated = len(_strip_punct(last_token)) <= 1 or last_token.endswith(".")
                if len(first_token) >= 4 and is_abbreviated and first_token in inbox_clean:
                    matched = True
                    break

        if matched:
            shortlist.append(pitch)
            logger.info(f"[REPLY CHECK] 🎯 Phase 1 match: pitch {pitch.id} ({contact.name})")
        else:
            # This pitch's contact did NOT appear in unread — mark checked, no reply
            _mark_checked_no_reply(pitch, db)

    logger.info(f"[REPLY CHECK] 📋 Phase 1 shortlist: {len(shortlist)} pitch(es) to deep-check.")
    return shortlist


# ── Phase 2: Targeted Thread Check ────────────────────────────────────────────

def _run_thread_check_phase(
    db: Session,
    pitches: list[SalesPitch],
    profile_id: int,
    cfg: dict,
    summary: dict,
) -> None:
    logger.info(f"[REPLY CHECK] 🔍 Phase 2: Deep-checking {len(pitches)} thread(s)…")

    for pitch in pitches:
        summary["checked"] += 1

        contact, contact_type = _resolve_contact(pitch, db)
        if not contact or not getattr(contact, "profile_url", None):
            logger.info(f"[REPLY CHECK] ⏭️  Pitch {pitch.id} — {contact_type} not found or no profile_url.")
            summary["skipped"] += 1
            _mark_checked_no_reply(pitch, db)
            continue

        # ── Call the Playwright scraper ────────────────────────────────────
        try:
            result = check_reply_for_contact(
                profile_url  = contact.profile_url,
                contact_name = contact.name or "Unknown",
                db           = db,
                delivered_at = pitch.delivered_at,
                profile_id   = profile_id,
            )

            messages = result.get("chat_history") or []

            if result["replied"]:
                _mark_replied(pitch, result["reply_text"] or "", db)
                summary["replied"] += 1
                logger.info(
                    f"[REPLY CHECK] 🎉 Reply recorded for pitch {pitch.id} "
                    f"({contact.name}): {(result['reply_text'] or '')[:60]!r}"
                )
                company_name = ""
                if pitch.business_client_id:
                    client = db.query(Business_Client).filter(
                        Business_Client.id == pitch.business_client_id
                    ).first()
                    company_name = client.name if client else ""

                # ── Generate LLM Suggested Reply using FULL Chat History ─────
                suggested_reply = generate_suggested_reply(
                    contact_name=contact.name or "Unknown",
                    company_name=company_name,
                    pitch_context=pitch.pitch_body or "",
                    messages=messages,
                )

                # ── Send notification and capture Telegram chat_id ──────────
                telegram_chat_id = notify_reply_received(
                    contact_name    = contact.name or "Unknown",
                    company_name    = company_name,
                    reply_snippet   = result["reply_text"] or "",
                    profile_id      = profile_id,
                    suggested_reply = suggested_reply,
                    sales_pitch_id  = pitch.id,
                )

                # ── Persist Chat History into client_chat_messages table ─────
                _sync_chat_history(
                    pitch=pitch,
                    contact=contact,
                    profile_id=profile_id,
                    messages=messages,
                    suggested_reply=suggested_reply,
                    telegram_chat_id=telegram_chat_id,
                    db=db,
                )

            else:
                _mark_checked_no_reply(pitch, db)
                _sync_chat_history(
                    pitch=pitch,
                    contact=contact,
                    profile_id=profile_id,
                    messages=messages,
                    suggested_reply=None,
                    telegram_chat_id=None,
                    db=db,
                )
                summary["no_reply"] += 1
                logger.info(f"[REPLY CHECK] ℹ️   No reply yet — pitch {pitch.id} ({contact.name})")

        except LinkedInSessionExpiredError:
            summary["session_error"] = True
            logger.info(
                "[REPLY CHECK] ⛔ LinkedIn session expired — stopping reply check.\n"
                "  Re-authenticate: python app/scraper/linkdin/save_state.py"
            )
            break

        except Exception as exc:
            logger.info(f"[REPLY CHECK] ❌ Unexpected error for pitch {pitch.id}: {exc}")
            _mark_checked_no_reply(pitch, db)
            summary["no_reply"] += 1

        # Polite delay between Playwright sessions
        time.sleep(cfg["delay_between"])



# ── Core logic ─────────────────────────────────────────────────────────────────

def check_pitch_replies(db: Session, profile_id: int = 1) -> dict:
    """
    Hybrid two-phase reply checker:
      Phase 1 — Scan inbox for unread conversations (1 page load).
      Phase 2 — Only open threads for contacts with new messages.
      Fallback — If inbox scan fails, use per-contact approach.
    """
    cfg = _cfg()
    summary = {
        "checked"      : 0,
        "replied"      : 0,
        "no_reply"     : 0,
        "skipped"      : 0,
        "session_error": False,
        "phase"        : "none",
    }

    pitches = _fetch_checkable_pitches(db, cfg["recheck_hours"], cfg["max_per_run"])

    if not pitches:
        logger.info("[REPLY CHECK] ℹ️   No pitches eligible for reply check.")
        return summary

    logger.info(f"[REPLY CHECK] 🔍 Starting hybrid reply check — {len(pitches)} pitch(es) eligible.")

    # ── Phase 1: Inbox scan ────────────────────────────────────────────────────
    try:
        shortlist = _run_inbox_scan_phase(db, pitches, profile_id, cfg)
    except LinkedInSessionExpiredError:
        summary["session_error"] = True
        logger.info(
            "[REPLY CHECK] ⛔ LinkedIn session expired during inbox scan.\n"
            "  Re-authenticate: python app/scraper/linkdin/save_state.py"
        )
        return summary

    if shortlist is not None:
        # Phase 1 succeeded
        summary["phase"] = "hybrid"
        if not shortlist:
            # No unread conversations matched — nothing to deep-check
            summary["no_reply"] = len(pitches)
            logger.info("[REPLY CHECK] ✅ Phase 1 found no unread matches — all pitches marked checked.")
        else:
            # Phase 2: deep-check only the shortlisted pitches
            _run_thread_check_phase(db, shortlist, profile_id, cfg, summary)
            # Mark remaining pitches (not in shortlist) as checked
            shortlist_ids = {p.id for p in shortlist}
            for pitch in pitches:
                if pitch.id not in shortlist_ids and not pitch.reply_checked_at:
                    _mark_checked_no_reply(pitch, db)
    else:
        # Phase 1 failed — fall back to per-contact check for all pitches
        summary["phase"] = "fallback"
        logger.info("[REPLY CHECK] 🔄 Falling back to per-contact reply check (Phase 1 failed).")
        _run_thread_check_phase(db, pitches, profile_id, cfg, summary)

    logger.info(
        f"[REPLY CHECK] ✅ Done (phase={summary['phase']}) — "
        f"checked: {summary['checked']} | "
        f"replied: {summary['replied']} | "
        f"no_reply: {summary['no_reply']} | "
        f"skipped: {summary['skipped']}"
    )
    return summary


# ── APScheduler entry point ────────────────────────────────────────────────────

def run_pitch_reply_check_job(profile_id: int = 1) -> None:
    """Sync entry point called by APScheduler."""
    db = SessionLocal()
    try:
        check_pitch_replies(db, profile_id=profile_id)
    except Exception as exc:
        logger.info(f"[REPLY CHECK] ❌ Unexpected error in reply check job: {exc}")
    finally:
        db.close()
