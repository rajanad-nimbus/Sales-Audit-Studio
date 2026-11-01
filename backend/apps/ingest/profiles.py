"""HTTP endpoints for source feed profile configuration and freshness."""

import uuid
from datetime import UTC, datetime

from database import get_db
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.database_errors import violates_unique_constraint
from apps.ingest.common import VALID_TYPES
from apps.ingest.contracts import (
    SourceFeedProfilePatch,
    SourceFeedProfileRequest,
    schedule_window_hours,
)
from apps.ingest.models import SourceFeedProfile, SourceRecord
from apps.security.authentication import require_role

router = APIRouter()


def profile_view(profile: SourceFeedProfile) -> dict:
    return {
        "id": profile.id,
        "name": profile.name,
        "source_system": profile.source_system,
        "schedule": profile.schedule,
        "column_mapping": profile.column_mapping,
        "enabled": profile.enabled,
        "updated_at": profile.updated_at,
    }


def _is_duplicate_profile_name(exc: IntegrityError) -> bool:
    return violates_unique_constraint(
        exc,
        constraint_names={"source_feed_profiles_name_key"},
        sqlite_messages={"UNIQUE constraint failed: source_feed_profiles.name"},
    )


@router.get("/api/ingest/profiles")
async def list_source_profiles(
    db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("it", "finance"))
):
    rows = (
        (await db.execute(select(SourceFeedProfile).order_by(SourceFeedProfile.name)))
        .scalars()
        .all()
    )
    return [profile_view(row) for row in rows]


@router.get("/api/ingest/feed-health")
async def feed_health(
    db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("it", "finance"))
):
    """Freshness for enabled source profiles, based solely on received immutable deliveries."""
    profiles = (
        (await db.execute(select(SourceFeedProfile).order_by(SourceFeedProfile.name)))
        .scalars()
        .all()
    )
    source_systems = {profile.source_system for profile in profiles if profile.source_system}
    newest_by_source = {}
    if source_systems:
        newest_rows = (
            await db.execute(
                select(SourceRecord.source_system, func.max(SourceRecord.received_at))
                .where(
                    SourceRecord.source_system.in_(source_systems),
                    SourceRecord.status.in_(("Archived", "Processed")),
                )
                .group_by(SourceRecord.source_system)
            )
        ).all()
        newest_by_source = dict(newest_rows)
    result = []
    now = datetime.now(UTC)
    for profile in profiles:
        newest = newest_by_source.get(profile.source_system)
        window = schedule_window_hours(profile.schedule)
        age = round((now - newest).total_seconds() / 3600, 1) if newest else None
        state = (
            "disabled"
            if not profile.enabled
            else "never"
            if not newest
            else "unknown"
            if not window
            else "overdue"
            if age > window
            else "fresh"
        )
        result.append(
            {
                "profile_id": profile.id,
                "name": profile.name,
                "source_system": profile.source_system,
                "schedule": profile.schedule,
                "last_received_at": newest,
                "age_hours": age,
                "expected_within_hours": window,
                "state": state,
            }
        )
    return result


@router.post("/api/ingest/profiles")
async def create_source_profile(
    body: SourceFeedProfileRequest,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("it")),
):
    if body.source_system not in VALID_TYPES:
        raise HTTPException(
            status_code=422, detail=f"source_system must be one of {sorted(VALID_TYPES)}"
        )
    profile = SourceFeedProfile(
        id=str(uuid.uuid4()),
        name=body.name.strip(),
        source_system=body.source_system,
        schedule=body.schedule,
        column_mapping=body.column_mapping,
        enabled=body.enabled,
    )
    db.add(profile)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if _is_duplicate_profile_name(exc):
            raise HTTPException(
                status_code=409, detail="A source-feed profile with this name already exists"
            ) from exc
        raise
    await db.refresh(profile)
    return profile_view(profile)


@router.patch("/api/ingest/profiles/{profile_id}")
async def update_source_profile(
    profile_id: str,
    body: SourceFeedProfilePatch,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("it")),
):
    profile = (
        await db.execute(select(SourceFeedProfile).where(SourceFeedProfile.id == profile_id))
    ).scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="Source-feed profile not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)
    await db.commit()
    await db.refresh(profile)
    return profile_view(profile)
