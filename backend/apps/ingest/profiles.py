"""HTTP endpoints for source feed profile configuration and freshness."""

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Literal

from database import get_db
from fastapi import APIRouter, Depends, HTTPException
from model_base import utcnow
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.audit.models import AuditEvent
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


class SourceFeedProfilePackage(BaseModel):
    format: Literal["zetsa.source-feed-profiles"]
    version: Literal[1]
    profiles: list[SourceFeedProfileRequest] = Field(max_length=500)

    @field_validator("profiles")
    @classmethod
    def unique_profile_names(cls, values: list[SourceFeedProfileRequest]):
        names = [profile.name.casefold() for profile in values]
        if len(names) != len(set(names)):
            raise ValueError("Profile names must be unique in the package")
        return values


class SourceFeedProfilePackagePreviewRequest(BaseModel):
    package: SourceFeedProfilePackage


class SourceFeedProfilePackageApplyRequest(SourceFeedProfilePackagePreviewRequest):
    preview_token: str = Field(pattern=r"^[a-f0-9]{64}$")
    reason: str = Field(min_length=10, max_length=2000)

    @field_validator("reason")
    @classmethod
    def valid_reason(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 10:
            raise ValueError("Reason must contain at least 10 non-whitespace characters")
        return normalized


def _profile_package_config(profile: SourceFeedProfile) -> dict:
    return {
        "name": profile.name,
        "source_system": profile.source_system,
        "schedule": profile.schedule,
        "column_mapping": profile.column_mapping or {},
    }


def _profile_audit_state(profile: SourceFeedProfile) -> dict:
    return {**_profile_package_config(profile), "enabled": profile.enabled}


def _profile_audit_event(
    *, actor: str, profile: SourceFeedProfile, description: str, before: dict | None
) -> AuditEvent:
    return AuditEvent(
        id=str(uuid.uuid4()),
        event_type="SOURCE_FEED_PROFILE_CHANGED",
        actor=actor,
        object_type="SourceFeedProfile",
        object_id=profile.id,
        action_description=description,
        before_state=before,
        after_state=_profile_audit_state(profile),
    )


def _package_config(package: SourceFeedProfilePackage) -> list[dict]:
    return [
        {
            "name": profile.name,
            "source_system": profile.source_system,
            "schedule": profile.schedule,
            "column_mapping": profile.column_mapping or {},
        }
        for profile in package.profiles
    ]


def _same_profile(profile: SourceFeedProfile, proposed: dict) -> bool:
    return _profile_package_config(profile) == proposed


def _profile_package_preview(
    package: SourceFeedProfilePackage, current: list[SourceFeedProfile]
) -> dict:
    by_name = {profile.name: profile for profile in current}
    rows = []
    counts = {"create": 0, "update": 0, "unchanged": 0}
    errors = []
    proposed_profiles = _package_config(package)
    for row_number, proposed in enumerate(proposed_profiles, start=1):
        if proposed["source_system"] not in VALID_TYPES:
            errors.append(
                {
                    "row": row_number,
                    "message": f"Unknown source system {proposed['source_system']!r}",
                }
            )
            continue
        existing = by_name.get(proposed["name"])
        action = (
            "Create"
            if existing is None
            else "Unchanged"
            if _same_profile(existing, proposed)
            else "Update"
        )
        counts[action.lower()] += 1
        rows.append(
            {
                "row": row_number,
                **proposed,
                "action": action,
                "enabled_after_import": existing.enabled if existing else False,
            }
        )
    token_data = {
        "package": proposed_profiles,
        "current": [
            {**_profile_package_config(profile), "enabled": profile.enabled}
            for profile in sorted(current, key=lambda item: item.name.casefold())
        ],
    }
    token = hashlib.sha256(
        json.dumps(token_data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "valid": not errors and bool(proposed_profiles),
        "preview_token": token if not errors and proposed_profiles else None,
        "errors": errors[:100],
        "errors_truncated": len(errors) > 100,
        "counts": counts,
        "rows": rows,
    }


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


@router.get("/api/ingest/profiles/package")
async def export_source_profile_package(
    db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("it", "finance"))
):
    rows = (
        (await db.execute(select(SourceFeedProfile).order_by(SourceFeedProfile.name))).scalars().all()
    )
    return {
        "format": "zetsa.source-feed-profiles",
        "version": 1,
        "profiles": [_profile_package_config(profile) for profile in rows],
    }


@router.post("/api/ingest/profiles/package/preview")
async def preview_source_profile_package(
    body: SourceFeedProfilePackagePreviewRequest,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("it")),
):
    current = (
        (await db.execute(select(SourceFeedProfile).order_by(SourceFeedProfile.name)))
        .scalars()
        .all()
    )
    return _profile_package_preview(body.package, current)


@router.post("/api/ingest/profiles/package/apply")
async def apply_source_profile_package(
    body: SourceFeedProfilePackageApplyRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("it")),
):
    current = (
        (
            await db.execute(
                select(SourceFeedProfile)
                .order_by(SourceFeedProfile.name)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    preview = _profile_package_preview(body.package, current)
    if not preview["valid"]:
        raise HTTPException(status_code=422, detail={"errors": preview["errors"]})
    if preview["preview_token"] != body.preview_token:
        raise HTTPException(
            status_code=409,
            detail="Source feed profiles changed after preview. Preview the package again.",
        )

    existing = {profile.name: profile for profile in current}
    counts = {"created": 0, "updated": 0, "unchanged": 0}
    now = utcnow()
    for proposed in _package_config(body.package):
        profile = existing.get(proposed["name"])
        action = (
            "unchanged"
            if profile is not None and _same_profile(profile, proposed)
            else "updated"
            if profile is not None
            else "created"
        )
        counts[action] += 1
        if action == "unchanged":
            continue

        before = (
            {**_profile_package_config(profile), "enabled": profile.enabled}
            if profile is not None
            else None
        )
        if profile is None:
            profile = SourceFeedProfile(
                id=str(uuid.uuid4()),
                name=proposed["name"],
                source_system=proposed["source_system"],
                schedule=proposed["schedule"],
                column_mapping=proposed["column_mapping"],
                enabled=False,
                created_at=now,
                updated_at=now,
            )
            db.add(profile)
            existing[profile.name] = profile
        else:
            profile.name = proposed["name"]
            profile.source_system = proposed["source_system"]
            profile.schedule = proposed["schedule"]
            profile.column_mapping = proposed["column_mapping"]
            profile.updated_at = now
        after = {**_profile_package_config(profile), "enabled": profile.enabled}
        db.add(
            AuditEvent(
                id=str(uuid.uuid4()),
                event_type="SOURCE_FEED_PROFILE_PACKAGE_APPLIED",
                actor=user.get("user") or "ontology.user",
                object_type="SourceFeedProfile",
                object_id=profile.id,
                action_description=f"Source feed profile package applied: {body.reason}",
                before_state=before,
                after_state=after,
            )
        )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if _is_duplicate_profile_name(exc):
            raise HTTPException(
                status_code=409,
                detail="A source feed profile name is already in use. Preview the package again.",
            ) from exc
        raise
    return counts


@router.post("/api/ingest/profiles")
async def create_source_profile(
    body: SourceFeedProfileRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("it")),
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
    db.add(
        _profile_audit_event(
            actor=user.get("user") or "ontology.user",
            profile=profile,
            description=f"Source feed profile created: {profile.name}",
            before=None,
        )
    )
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
    user: dict = Depends(require_role("it")),
):
    profile = (
        await db.execute(
            select(SourceFeedProfile)
            .where(SourceFeedProfile.id == profile_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="Source-feed profile not found")
    before = _profile_audit_state(profile)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)
    after = _profile_audit_state(profile)
    if before != after:
        action = (
            "enabled"
            if not before["enabled"] and after["enabled"]
            else "disabled"
            if before["enabled"] and not after["enabled"]
            else "updated"
        )
        db.add(
            _profile_audit_event(
                actor=user.get("user") or "ontology.user",
                profile=profile,
                description=f"Source feed profile {action}: {profile.name}",
                before=before,
            )
        )
    await db.commit()
    await db.refresh(profile)
    return profile_view(profile)
