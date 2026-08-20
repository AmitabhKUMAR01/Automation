from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config.database import get_db
from app.models.linkedin_search_contact import LinkedinSearchContact
from app.models.profile_setting import ProfileSetting
from app.models.sales_pitch import SalesPitch
from app.utils.response import error, success


router = APIRouter(prefix="/profile", tags=["Profile"])


def _timeframe_start(timeframe: str) -> Optional[datetime]:
    if timeframe == "7d":
        return datetime.now() - timedelta(days=7)
    if timeframe == "30d":
        return datetime.now() - timedelta(days=30)
    if timeframe == "all":
        return None
    raise ValueError("Invalid timeframe. Use 7d, 30d, or all.")


def _as_naive(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        return dt
    return dt.replace(tzinfo=None)


def _infer_profile_status(profile: ProfileSetting) -> str:
    if not profile.is_active:
        return "inactive"

    resets_at = _as_naive(profile.weekly_limit_resets_at)
    if profile.weekly_limit_reached_at and (
        resets_at is None
        or resets_at > datetime.now()
    ):
        return "cooldown"

    return "active"


def _matching_profile_ids(
    db: Session,
    profile_id: str,
    profile_status: str,
) -> list[int]:
    profiles = db.query(ProfileSetting).all()

    if profile_id != "all":
        selected_id = int(profile_id)
        profiles = [profile for profile in profiles if profile.id == selected_id]

    if profile_status != "all":
        profiles = [
            profile
            for profile in profiles
            if _infer_profile_status(profile) == profile_status
        ]

    return [profile.id for profile in profiles]


def _text_equals(column, value: str):
    return func.lower(column) == value.strip().lower()


def _profile_scope_for_filters(
    db: Session,
    profile_id: str,
    profile_status: str,
    position: str,
    location: str,
):
    profile_ids = _matching_profile_ids(db, profile_id, profile_status)

    contact_filters = []
    if profile_id != "all" or profile_status != "all":
        if not profile_ids:
            return [], contact_filters
        contact_filters.append(LinkedinSearchContact.profile_id.in_(profile_ids))

    if position != "all":
        contact_filters.append(_text_equals(LinkedinSearchContact.position, position))

    if location != "all":
        contact_filters.append(_text_equals(LinkedinSearchContact.location, location))

    return profile_ids, contact_filters


def _build_kpis(
    db: Session,
    profile_id: str = "all",
    timeframe: str = "30d",
    position: str = "all",
    location: str = "all",
    job_type: str = "all",
    job_status: str = "all",
    profile_status: str = "all",
):
    start_at = _timeframe_start(timeframe)
    profile_ids, contact_filters = _profile_scope_for_filters(
        db=db,
        profile_id=profile_id,
        profile_status=profile_status,
        position=position,
        location=location,
    )

    if profile_id != "all" and not profile_ids:
        return {
            "connectionsSent": 0,
            "connectionsAccepted": 0,
            "acceptanceRate": 0,
            "messagesSent": 0,
            "repliesReceived": 0,
            "replyRate": 0,
            "dailyQuotaUsage": 0,
            "profileHealth": 0,
        }

    sent_query = db.query(func.count(LinkedinSearchContact.id)).filter(
        LinkedinSearchContact.connection_sent.is_(True),
        *contact_filters,
    )
    if start_at:
        sent_query = sent_query.filter(LinkedinSearchContact.connection_sent_at >= start_at)

    accepted_query = db.query(func.count(LinkedinSearchContact.id)).filter(
        LinkedinSearchContact.is_connected.is_(True),
        *contact_filters,
    )
    if start_at:
        accepted_query = accepted_query.filter(LinkedinSearchContact.connected_at >= start_at)

    pitch_query = (
        db.query(func.count(SalesPitch.id))
        .join(
            LinkedinSearchContact,
            SalesPitch.linkedin_search_contact_id == LinkedinSearchContact.id,
        )
        .filter(
            SalesPitch.pitch_channel == "linkedin",
            SalesPitch.delivery_status == "sent",
            *contact_filters,
        )
    )
    if start_at:
        pitch_query = pitch_query.filter(
            func.coalesce(SalesPitch.delivered_at, SalesPitch.created_at) >= start_at
        )

    reply_query = (
        db.query(func.count(SalesPitch.id))
        .join(
            LinkedinSearchContact,
            SalesPitch.linkedin_search_contact_id == LinkedinSearchContact.id,
        )
        .filter(
            SalesPitch.reply_received.is_(True),
            *contact_filters,
        )
    )
    if start_at:
        reply_query = reply_query.filter(
            func.coalesce(
                SalesPitch.replied_at,
                SalesPitch.delivered_at,
                SalesPitch.created_at,
            )
            >= start_at
        )

    connections_sent = int(sent_query.scalar() or 0)
    connections_accepted = int(accepted_query.scalar() or 0)
    messages_sent = int(pitch_query.scalar() or 0)
    replies_received = int(reply_query.scalar() or 0)

    acceptance_rate = round((connections_accepted / connections_sent) * 100, 1) if connections_sent else 0
    reply_rate = round((replies_received / messages_sent) * 100, 1) if messages_sent else 0

    if profile_id == "all" and profile_status == "all":
        matched_profiles = db.query(ProfileSetting).all()
    else:
        matched_profiles = db.query(ProfileSetting).filter(ProfileSetting.id.in_(profile_ids)).all() if profile_ids else []

    if start_at and matched_profiles:
        window_days = max(1, (datetime.now() - start_at).days)
    else:
        earliest_contact = (
            db.query(func.min(LinkedinSearchContact.connection_sent_at))
            .filter(LinkedinSearchContact.connection_sent.is_(True), *contact_filters)
            .scalar()
        )
        earliest_pitch = (
            db.query(func.min(SalesPitch.delivered_at))
            .join(
                LinkedinSearchContact,
                SalesPitch.linkedin_search_contact_id == LinkedinSearchContact.id,
            )
            .filter(
                SalesPitch.pitch_channel == "linkedin",
                SalesPitch.delivery_status == "sent",
                *contact_filters,
            )
            .scalar()
        )
        earliest = min(
            [dt for dt in [_as_naive(earliest_contact), _as_naive(earliest_pitch)] if dt is not None],
            default=None,
        )
        window_days = max(1, (datetime.now() - earliest).days + 1) if earliest else 1

    quota_samples = []
    for profile in matched_profiles:
        quota_filters = [
            LinkedinSearchContact.profile_id == profile.id,
            LinkedinSearchContact.connection_sent.is_(True),
        ]
        if position != "all":
            quota_filters.append(_text_equals(LinkedinSearchContact.position, position))
        if location != "all":
            quota_filters.append(_text_equals(LinkedinSearchContact.location, location))
        if start_at:
            quota_filters.append(LinkedinSearchContact.connection_sent_at >= start_at)

        profile_connections = (
            db.query(func.count(LinkedinSearchContact.id))
            .filter(*quota_filters)
            .scalar()
            or 0
        )
        daily_limit = profile.max_connections_per_day or 0
        if daily_limit > 0:
            quota_samples.append((profile_connections / max(1, daily_limit * window_days)) * 100)

    daily_quota_usage = round(sum(quota_samples) / len(quota_samples), 1) if quota_samples else 0
    profile_health = round(
        min(
            100,
            (acceptance_rate * 0.4)
            + (reply_rate * 0.4)
            + (min(daily_quota_usage, 100) * 0.2),
        ),
        1,
    )

    return {
        "connectionsSent": connections_sent,
        "connectionsAccepted": connections_accepted,
        "acceptanceRate": acceptance_rate,
        "messagesSent": messages_sent,
        "repliesReceived": replies_received,
        "replyRate": reply_rate,
        "dailyQuotaUsage": daily_quota_usage,
        "profileHealth": profile_health,
    }


@router.get("/connectionsent")
def get_connections_sent(db: Session = Depends(get_db)):
    try:
        connections_sent = (
            db.query(LinkedinSearchContact)
            .filter(LinkedinSearchContact.connection_sent.is_(True))
            .order_by(LinkedinSearchContact.connection_sent_at.desc().nullslast())
            .all()
        )
        data = [
            {
                "id": contact.id,
                "uuid": contact.uuid,
                "profile_id": contact.profile_id,
                "name": contact.name,
                "position": contact.position,
                "location": contact.location,
                "profile_url": contact.profile_url,
                "source": contact.source,
                "connection_sent": contact.connection_sent,
                "connection_sent_at": contact.connection_sent_at,
                "is_connected": contact.is_connected,
                "connected_at": contact.connected_at,
                "contact_messaged_first": contact.contact_messaged_first,
                "created_at": contact.created_at,
                "exported_at": contact.exported_at,
            }
            for contact in connections_sent
        ]
        return success(
            {"items": data, "total": len(data)},
            "Connections sent fetched successfully",
        )
    except Exception as exc:
        return error(f"Failed to retrieve connections sent: {exc}")


@router.get("/kpis")
def get_kpis(
    profile_id: str = Query("all", alias="profileId"),
    timeframe: str = Query("30d"),
    position: str = Query("all"),
    location: str = Query("all"),
    job_type: str = Query("all", alias="jobType"),
    job_status: str = Query("all", alias="jobStatus"),
    profile_status: str = Query("all", alias="profileStatus"),
    db: Session = Depends(get_db),
):
    try:
        kpis = _build_kpis(
            db=db,
            profile_id=profile_id,
            timeframe=timeframe,
            position=position,
            location=location,
            job_type=job_type,
            job_status=job_status,
            profile_status=profile_status,
        )
        return success(
            {
                "filters": {
                    "profileId": profile_id,
                    "timeframe": timeframe,
                    "position": position,
                    "location": location,
                    "jobType": job_type,
                    "jobStatus": job_status,
                    "profileStatus": profile_status,
                },
                "kpis": kpis,
            },
            "Profile KPI metrics fetched successfully",
        )
    except ValueError as exc:
        return error(str(exc), status_code=400)
    except Exception as exc:
        return error(f"Failed to fetch profile KPI metrics: {exc}")


def _build_funnel(
    db: Session,
    profile_id: str = "all",
    timeframe: str = "30d",
    position: str = "all",
    location: str = "all",
    job_type: str = "all",
    job_status: str = "all",
    profile_status: str = "all",
):
    start_at = _timeframe_start(timeframe)
    profile_ids, contact_filters = _profile_scope_for_filters(
        db=db,
        profile_id=profile_id,
        profile_status=profile_status,
        position=position,
        location=location,
    )

    if profile_id != "all" and not profile_ids:
        return [
            {"key": "sent", "label": "Connections Sent", "value": 0, "conversionFromPrev": None},
            {"key": "accepted", "label": "Connections Accepted", "value": 0, "conversionFromPrev": 0.0},
            {"key": "dms", "label": "DMs Sent", "value": 0, "conversionFromPrev": 0.0},
            {"key": "replies", "label": "Replies Received", "value": 0, "conversionFromPrev": 0.0},
        ]

    sent_query = db.query(func.count(LinkedinSearchContact.id)).filter(
        LinkedinSearchContact.connection_sent.is_(True),
        *contact_filters,
    )
    if start_at:
        sent_query = sent_query.filter(LinkedinSearchContact.connection_sent_at >= start_at)

    accepted_query = db.query(func.count(LinkedinSearchContact.id)).filter(
        LinkedinSearchContact.is_connected.is_(True),
        *contact_filters,
    )
    if start_at:
        accepted_query = accepted_query.filter(LinkedinSearchContact.connected_at >= start_at)

    pitch_query = (
        db.query(func.count(SalesPitch.id))
        .join(
            LinkedinSearchContact,
            SalesPitch.linkedin_search_contact_id == LinkedinSearchContact.id,
        )
        .filter(
            SalesPitch.pitch_channel == "linkedin",
            SalesPitch.delivery_status == "sent",
            *contact_filters,
        )
    )
    if start_at:
        pitch_query = pitch_query.filter(
            func.coalesce(SalesPitch.delivered_at, SalesPitch.created_at) >= start_at
        )

    reply_query = (
        db.query(func.count(SalesPitch.id))
        .join(
            LinkedinSearchContact,
            SalesPitch.linkedin_search_contact_id == LinkedinSearchContact.id,
        )
        .filter(
            SalesPitch.reply_received.is_(True),
            *contact_filters,
        )
    )
    if start_at:
        reply_query = reply_query.filter(
            func.coalesce(
                SalesPitch.replied_at,
                SalesPitch.delivered_at,
                SalesPitch.created_at,
            )
            >= start_at
        )

    connections_sent = int(sent_query.scalar() or 0)
    connections_accepted = int(accepted_query.scalar() or 0)
    messages_sent = int(pitch_query.scalar() or 0)
    replies_received = int(reply_query.scalar() or 0)

    # Conversion rate calculations
    acceptance_rate = round((connections_accepted / connections_sent) * 100, 1) if connections_sent else 0.0
    dm_rate = round((messages_sent / connections_accepted) * 100, 1) if connections_accepted else 0.0
    reply_rate = round((replies_received / messages_sent) * 100, 1) if messages_sent else 0.0

    return [
        {
            "key": "sent",
            "label": "Connections Sent",
            "value": connections_sent,
            "conversionFromPrev": None,
        },
        {
            "key": "accepted",
            "label": "Connections Accepted",
            "value": connections_accepted,
            "conversionFromPrev": acceptance_rate,
        },
        {
            "key": "dms",
            "label": "DMs Sent",
            "value": messages_sent,
            "conversionFromPrev": dm_rate,
        },
        {
            "key": "replies",
            "label": "Replies Received",
            "value": replies_received,
            "conversionFromPrev": reply_rate,
        },
    ]


@router.get("/funnel")
def get_funnel(
    profile_id: str = Query("all", alias="profileId"),
    timeframe: str = Query("30d"),
    position: str = Query("all"),
    location: str = Query("all"),
    job_type: str = Query("all", alias="jobType"),
    job_status: str = Query("all", alias="jobStatus"),
    profile_status: str = Query("all", alias="profileStatus"),
    db: Session = Depends(get_db),
):
    try:
        funnel = _build_funnel(
            db=db,
            profile_id=profile_id,
            timeframe=timeframe,
            position=position,
            location=location,
            job_type=job_type,
            job_status=job_status,
            profile_status=profile_status,
        )
        return success(
            funnel,
            "Outreach funnel stages fetched successfully",
        )
    except ValueError as exc:
        return error(str(exc), status_code=400)
    except Exception as exc:
        return error(f"Failed to fetch outreach funnel stages: {exc}")
