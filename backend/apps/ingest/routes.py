import csv
import io
import uuid
from decimal import Decimal
from datetime import datetime, timedelta, timezone
import re
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import case, func, select, tuple_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.database_errors import violates_unique_constraint
from apps.security import scope
from apps.ingest import reconciliation
from apps.ingest import service as ingest
from apps.ingest.common import VALID_TYPES
from apps.ingest.simulation import simulate_feed
from apps.ingest import source_settings
from apps.ingest.shopify import scheduler
from apps.ingest.limits import MAX_DELIVERY_BYTES, MAX_DELIVERY_RECORDS
from apps.ingest.manifests import validate_business_date
from apps.totals.currency import validate_currency
from integrations.ontology.nimbus import audit
from auth import require_role
from database import get_db
from models import CanonicalTransaction, ReconciliationMatch, ReconciliationPolicy, SourceFeedProfile, SourceRecord, SourceSetting, utcnow

router = APIRouter()


def json_payload_size(value, limit: int) -> int:
    """Count compact UTF-8 JSON bytes without building a second full serialized payload."""
    encoder = json.JSONEncoder(separators=(",", ":"), ensure_ascii=False)
    size = 0
    for chunk in encoder.iterencode(value):
        size += len(chunk.encode("utf-8"))
        if size > limit:
            return size
    return size


class IngestRequest(BaseModel):
    source_system: str = Field(min_length=1, max_length=50)
    delivery_id: str = Field(min_length=1, max_length=255)
    records: list[dict] = Field(max_length=MAX_DELIVERY_RECORDS)

    @field_validator("source_system", "delivery_id")
    @classmethod
    def reject_blank_identifiers(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @model_validator(mode="after")
    def enforce_payload_size(self):
        if json_payload_size(self.records, MAX_DELIVERY_BYTES) > MAX_DELIVERY_BYTES:
            raise ValueError(f"Delivery payload exceeds {MAX_DELIVERY_BYTES} bytes")
        return self


class CsvIngestRequest(BaseModel):
    source_system: str = Field(min_length=1, max_length=50)
    delivery_id: str = Field(min_length=1, max_length=255)
    csv_data: str = Field(max_length=MAX_DELIVERY_BYTES)
    profile_id: str | None = None

    @field_validator("source_system", "delivery_id")
    @classmethod
    def reject_blank_identifiers(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @model_validator(mode="after")
    def enforce_payload_size(self):
        if len(self.csv_data.encode("utf-8")) > MAX_DELIVERY_BYTES:
            raise ValueError(f"CSV payload exceeds {MAX_DELIVERY_BYTES} bytes")
        return self


def validate_profile_mapping(value: dict[str, str] | None) -> dict[str, str] | None:
    if value is None:
        return None
    normalized = {}
    for canonical, source_column in value.items():
        target, source = canonical.strip(), source_column.strip()
        if not target or not source:
            raise ValueError("Column mapping keys and values must not be blank")
        if len(target) > 100 or len(source) > 100:
            raise ValueError("Column mapping keys and values must be at most 100 characters")
        if target in normalized:
            raise ValueError("Column mapping keys must remain unique after trimming")
        normalized[target] = source
    return normalized


def parse_csv_delivery(csv_data: str, mapping: dict[str, str] | None = None) -> list[dict[str, str | None]]:
    """Parse at most the configured record cap, applying mappings without duplicating the raw row set."""
    rows = []
    try:
        for index, row in enumerate(csv.DictReader(io.StringIO(csv_data))):
            if index >= MAX_DELIVERY_RECORDS:
                raise ValueError(f"CSV exceeds the {MAX_DELIVERY_RECORDS:,}-record delivery limit")
            rows.append(
                {canonical: row.get(source_column) for canonical, source_column in mapping.items()}
                if mapping else row
            )
    except csv.Error as exc:
        raise ValueError(f"Invalid CSV: {exc}") from exc
    if not rows:
        raise ValueError("CSV contains no data rows")
    return rows


class SourceFeedProfileRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    source_system: str = Field(min_length=1, max_length=32)
    schedule: str | None = Field(default=None, max_length=128)
    column_mapping: dict[str, str] = Field(default_factory=dict, max_length=100)
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def normalize_profile_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Profile name cannot be blank")
        return normalized

    @field_validator("schedule")
    @classmethod
    def normalize_schedule(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @field_validator("column_mapping")
    @classmethod
    def validate_column_mapping(cls, value: dict[str, str]) -> dict[str, str]:
        return validate_profile_mapping(value) or {}


class SourceFeedProfilePatch(BaseModel):
    schedule: str | None = Field(default=None, max_length=128)
    column_mapping: dict[str, str] | None = Field(default=None, max_length=100)
    enabled: bool | None = None

    @field_validator("schedule")
    @classmethod
    def normalize_schedule(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @field_validator("column_mapping")
    @classmethod
    def validate_column_mapping(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        return validate_profile_mapping(value)

    @model_validator(mode="after")
    def reject_null_mapping(self):
        if "column_mapping" in self.model_fields_set and self.column_mapping is None:
            raise ValueError("column_mapping must be an object when provided")
        return self


class ReconciliationPolicyRequest(BaseModel):
    amount_tolerance: Decimal = Decimal("0")
    amount_tolerance_currency: str = "USD"
    settlement_day_tolerance: int = 0
    gap_min_count: int = 10
    gap_share: Decimal = Decimal("0.1")


class ManualMatchRequest(BaseModel):
    transaction_ids: list[Annotated[str, Field(min_length=1, max_length=36)]] = Field(min_length=2, max_length=100)
    match_type: str = Field(default="Manual Match", min_length=1, max_length=50)
    rationale: str = Field(min_length=1, max_length=2000)


def profile_view(profile: SourceFeedProfile) -> dict:
    return {"id": profile.id, "name": profile.name, "source_system": profile.source_system,
            "schedule": profile.schedule, "column_mapping": profile.column_mapping,
            "enabled": profile.enabled, "updated_at": profile.updated_at}


def _is_duplicate_profile_name(exc: IntegrityError) -> bool:
    return violates_unique_constraint(
        exc,
        constraint_names={"source_feed_profiles_name_key"},
        sqlite_messages={"UNIQUE constraint failed: source_feed_profiles.name"},
    )


def _is_active_reconciliation_policy_conflict(exc: IntegrityError) -> bool:
    return violates_unique_constraint(
        exc,
        constraint_names={"uq_reconciliation_policies_one_active"},
        sqlite_messages={"UNIQUE constraint failed: reconciliation_policies.active"},
    )


def schedule_window_hours(schedule: str | None) -> int | None:
    """Interpret a small, explicit cadence vocabulary without guessing cron syntax."""
    value = (schedule or "").strip().lower()
    if not value:
        return None
    if "hour" in value:
        return 2
    if "daily" in value or "day" in value:
        return 26
    if "weekly" in value or "week" in value:
        return 8 * 24
    return None


@router.get("/api/ingest/profiles")
async def list_source_profiles(db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("it", "finance"))):
    rows = (await db.execute(select(SourceFeedProfile).order_by(SourceFeedProfile.name))).scalars().all()
    return [profile_view(row) for row in rows]


@router.get("/api/ingest/deliveries")
async def delivery_history(limit: Annotated[int, Query(ge=1, le=100)] = 25, db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("it", "finance"))):
    """Operational history grouped by immutable source delivery ID."""
    recent = (select(
        SourceRecord.source_system.label("source_system"),
        SourceRecord.delivery_id.label("delivery_id"),
        func.max(SourceRecord.received_at).label("received_at"),
    ).group_by(SourceRecord.source_system, SourceRecord.delivery_id)
        .order_by(func.max(SourceRecord.received_at).desc(), SourceRecord.source_system, SourceRecord.delivery_id)
        .limit(limit).subquery())
    delivery_keys = (await db.execute(select(recent.c.source_system, recent.c.delivery_id, recent.c.received_at))).all()
    if not delivery_keys:
        return []

    keys = [(source_system, delivery_id) for source_system, delivery_id, _ in delivery_keys]
    counts = (await db.execute(
        select(
            SourceRecord.source_system,
            SourceRecord.delivery_id,
            func.count().label("records"),
            func.sum(case((SourceRecord.status == "Processed", 1), else_=0)).label("processed"),
            func.sum(case((SourceRecord.status == "Quarantined", 1), else_=0)).label("quarantined"),
        ).where(tuple_(SourceRecord.source_system, SourceRecord.delivery_id).in_(keys))
        .group_by(SourceRecord.source_system, SourceRecord.delivery_id)
    )).all()

    ranked_errors = select(
        SourceRecord.source_system.label("source_system"),
        SourceRecord.delivery_id.label("delivery_id"),
        SourceRecord.quarantine_reason.label("last_error"),
        func.row_number().over(
            partition_by=(SourceRecord.source_system, SourceRecord.delivery_id),
            order_by=(SourceRecord.received_at.desc(), SourceRecord.id.desc()),
        ).label("row_number"),
    ).where(
        tuple_(SourceRecord.source_system, SourceRecord.delivery_id).in_(keys),
        SourceRecord.status == "Quarantined",
    ).subquery()
    errors = (await db.execute(select(ranked_errors.c.source_system, ranked_errors.c.delivery_id,
                                      ranked_errors.c.last_error)
                               .where(ranked_errors.c.row_number == 1))).all()
    count_by_key = {(source, delivery): (records, processed, quarantined)
                    for source, delivery, records, processed, quarantined in counts}
    error_by_key = {(source, delivery): error for source, delivery, error in errors}
    result = []
    for source, delivery, received_at in delivery_keys:
        records, processed, quarantined = count_by_key[(source, delivery)]
        result.append({"source_system": source, "delivery_id": delivery, "received_at": received_at,
                       "records": records, "processed": processed, "quarantined": quarantined,
                       "last_error": error_by_key.get((source, delivery))})
    return result


@router.get("/api/ingest/feed-health")
async def feed_health(db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("it", "finance"))):
    """Freshness for enabled source profiles, based solely on received immutable deliveries."""
    profiles = (await db.execute(select(SourceFeedProfile).order_by(SourceFeedProfile.name))).scalars().all()
    source_systems = {profile.source_system for profile in profiles if profile.source_system}
    newest_by_source = {}
    if source_systems:
        newest_rows = (await db.execute(
            select(SourceRecord.source_system, func.max(SourceRecord.received_at))
            .where(
                SourceRecord.source_system.in_(source_systems),
                SourceRecord.status.in_(("Archived", "Processed")),
            )
            .group_by(SourceRecord.source_system)
        )).all()
        newest_by_source = dict(newest_rows)
    result = []
    now = datetime.now(timezone.utc)
    for profile in profiles:
        newest = newest_by_source.get(profile.source_system)
        window = schedule_window_hours(profile.schedule)
        age = round((now - newest).total_seconds() / 3600, 1) if newest else None
        state = "disabled" if not profile.enabled else "never" if not newest else "unknown" if not window else "overdue" if age > window else "fresh"
        result.append({"profile_id": profile.id, "name": profile.name, "source_system": profile.source_system,
                       "schedule": profile.schedule, "last_received_at": newest, "age_hours": age,
                       "expected_within_hours": window, "state": state})
    return result


@router.get("/api/reconciliation/policy")
async def get_reconciliation_policy(db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("finance", "it"))):
    policy = (await db.execute(select(ReconciliationPolicy).where(ReconciliationPolicy.active == True)
                               .order_by(ReconciliationPolicy.updated_at.desc(), ReconciliationPolicy.id.desc())
                               .limit(1))).scalar_one_or_none()  # noqa: E712
    return {"source": "local-fallback", "amount_tolerance": policy.amount_tolerance if policy else 0,
            "amount_tolerance_currency": policy.amount_tolerance_currency if policy else "USD",
            "settlement_day_tolerance": policy.settlement_day_tolerance if policy else 0,
            "gap_min_count": policy.gap_min_count if policy else 10, "gap_share": policy.gap_share if policy else 0.1,
            "updated_by": policy.updated_by if policy else None, "updated_at": policy.updated_at if policy else None}


@router.put("/api/reconciliation/policy")
async def update_reconciliation_policy(body: ReconciliationPolicyRequest, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance"))):
    try: validate_currency(body.amount_tolerance_currency)
    except ValueError as exc: raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not body.amount_tolerance.is_finite() or body.amount_tolerance < 0 or body.amount_tolerance > Decimal("9999999999999.99") or body.amount_tolerance != body.amount_tolerance.quantize(Decimal("0.01")) or body.settlement_day_tolerance < 0 or body.settlement_day_tolerance > 31:
        raise HTTPException(status_code=422, detail="Tolerance values must be non-negative; settlement days may not exceed 31")
    if not body.gap_share.is_finite() or body.gap_min_count < 1 or not 0 < body.gap_share <= 1 or body.gap_share != body.gap_share.quantize(Decimal("0.0001")):
        raise HTTPException(status_code=422, detail="Gap minimum must be at least 1 and gap share between 0 and 1")
    policy_query = (select(ReconciliationPolicy).where(ReconciliationPolicy.active == True)
                    .order_by(ReconciliationPolicy.updated_at.desc(), ReconciliationPolicy.id.desc())
                    .limit(1).with_for_update())  # noqa: E712
    policy = (await db.execute(policy_query)).scalar_one_or_none()
    if not policy:
        policy = ReconciliationPolicy(id=str(uuid.uuid4()), name="Nimbus fallback reconciliation policy")
        db.add(policy)
    policy.amount_tolerance, policy.settlement_day_tolerance = body.amount_tolerance, body.settlement_day_tolerance
    policy.amount_tolerance_currency = body.amount_tolerance_currency
    policy.gap_min_count, policy.gap_share = body.gap_min_count, Decimal(str(body.gap_share))
    policy.updated_by, policy.updated_at = user["user"] or "ontology.user", utcnow()
    try:
        await db.commit()
    except IntegrityError as exc:
        # Concurrent first writes can race to create the fallback row. The active
        # policy index serializes the winner; apply this request to that row.
        await db.rollback()
        if not _is_active_reconciliation_policy_conflict(exc):
            raise
        policy = (await db.execute(policy_query)).scalar_one_or_none()
        if policy is None:
            raise HTTPException(status_code=409, detail="Reconciliation policy changed concurrently")
        policy.amount_tolerance, policy.settlement_day_tolerance = body.amount_tolerance, body.settlement_day_tolerance
        policy.amount_tolerance_currency = body.amount_tolerance_currency
        policy.gap_min_count, policy.gap_share = body.gap_min_count, Decimal(str(body.gap_share))
        policy.updated_by, policy.updated_at = user["user"] or "ontology.user", utcnow()
        await db.commit()
    return {"source": "local-fallback", "amount_tolerance": policy.amount_tolerance, "amount_tolerance_currency": policy.amount_tolerance_currency, "settlement_day_tolerance": policy.settlement_day_tolerance, "gap_min_count": policy.gap_min_count, "gap_share": policy.gap_share, "updated_by": policy.updated_by, "updated_at": policy.updated_at}


@router.get("/api/reconciliation/matches")
async def list_manual_matches(status: str | None = None, db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("finance", "it"))):
    query = select(ReconciliationMatch).order_by(ReconciliationMatch.created_at.desc(), ReconciliationMatch.id.desc())
    if status: query = query.where(ReconciliationMatch.status == status)
    stores = scope.allowed_stores(_)
    if stores is not None:
        if not stores:
            return []
        query = query.where(ReconciliationMatch.store_id.in_(stores))
    rows = (await db.execute(query.limit(100))).scalars().all()
    return [{"id": row.id, "transaction_ids": row.transaction_ids, "match_type": row.match_type, "status": row.status, "rationale": row.rationale, "proposed_by": row.proposed_by, "reviewed_by": row.reviewed_by, "created_at": row.created_at} for row in rows]


async def authorized_match_transactions(db: AsyncSession, match: ReconciliationMatch, user: dict, *, lock: bool = False) -> list[CanonicalTransaction]:
    ids = match.transaction_ids
    if not isinstance(ids, list) or len(ids) < 2 or any(not isinstance(item, str) for item in ids) or len(set(ids)) != len(ids):
        raise HTTPException(status_code=409, detail="Match transaction references are invalid")
    query = select(CanonicalTransaction).where(CanonicalTransaction.id.in_(ids)).order_by(CanonicalTransaction.id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    rows = (await db.execute(query)).scalars().all()
    if len(rows) != len(ids):
        raise HTTPException(status_code=409, detail="Match source records are no longer available")
    for row in rows:
        scope.require_store(user, row.store_id)
    if len({row.store_id for row in rows}) != 1 or len({row.currency for row in rows}) != 1:
        raise HTTPException(status_code=409, detail="Match transactions must use one store and one currency")
    return list(rows)


@router.post("/api/reconciliation/matches")
async def propose_manual_match(body: ManualMatchRequest, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance", "it"))):
    ids = list(dict.fromkeys(body.transaction_ids))
    if len(ids) < 2 or not body.rationale.strip(): raise HTTPException(status_code=422, detail="At least two transactions and a rationale are required")
    rows = (await db.execute(select(CanonicalTransaction).where(CanonicalTransaction.id.in_(ids)))).scalars().all()
    if len(rows) != len(ids): raise HTTPException(status_code=404, detail="One or more transactions were not found")
    for row in rows: scope.require_store(user, row.store_id)
    if len({row.store_id for row in rows}) != 1 or len({row.currency for row in rows}) != 1:
        raise HTTPException(status_code=422, detail="Manual matches must use one store and one currency")
    if any(row.reconciliation_status not in {"Unmatched", "Exception"} for row in rows):
        raise HTTPException(status_code=409, detail="One or more transactions have already been reconciled")
    match = ReconciliationMatch(id=str(uuid.uuid4()), transaction_ids=ids, store_id=rows[0].store_id,
                                match_type=body.match_type.strip() or "Manual Match",
                                rationale=body.rationale.strip(), proposed_by=user["user"] or "ontology.user")
    db.add(match); audit(db, "MANUAL_MATCH_PROPOSED", None, "ReconciliationMatch", match.id, "Manual reconciliation match proposed", actor=match.proposed_by); await db.commit()
    return {"id": match.id, "status": match.status}


@router.post("/api/reconciliation/matches/{match_id}/approve")
async def approve_manual_match(match_id: str, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance"))):
    match = (await db.execute(select(ReconciliationMatch).where(ReconciliationMatch.id == match_id))).scalar_one_or_none()
    if not match or match.status != "Proposed": raise HTTPException(status_code=409, detail="Proposed manual match not found")
    rows = await authorized_match_transactions(db, match, user, lock=True)
    if any(row.reconciliation_status not in {"Unmatched", "Exception"} for row in rows):
        raise HTTPException(status_code=409, detail="One or more transactions have already been reconciled")
    claimed = (await db.execute(update(ReconciliationMatch).where(
        ReconciliationMatch.id == match_id, ReconciliationMatch.status == "Proposed"
    ).values(status="Approved", reviewed_by=user["user"] or "ontology.user", reviewed_at=utcnow())
        .returning(ReconciliationMatch.id))).scalar_one_or_none()
    if not claimed: raise HTTPException(status_code=409, detail="Match was already reviewed")
    for row in rows: row.reconciliation_status, row.disposition = "Manually Matched", "Closed"
    audit(db, "MANUAL_MATCH_APPROVED", None, "ReconciliationMatch", match.id, "Manual reconciliation match approved", actor=match.reviewed_by); await db.commit(); return {"id": match.id, "status": match.status}


@router.post("/api/reconciliation/matches/{match_id}/reject")
async def reject_manual_match(match_id: str, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance"))):
    match = (await db.execute(select(ReconciliationMatch).where(ReconciliationMatch.id == match_id))).scalar_one_or_none()
    if not match or match.status != "Proposed": raise HTTPException(status_code=409, detail="Proposed manual match not found")
    await authorized_match_transactions(db, match, user)
    claimed = (await db.execute(update(ReconciliationMatch).where(
        ReconciliationMatch.id == match_id, ReconciliationMatch.status == "Proposed"
    ).values(status="Rejected", reviewed_by=user["user"] or "ontology.user", reviewed_at=utcnow())
        .returning(ReconciliationMatch.id))).scalar_one_or_none()
    if not claimed: raise HTTPException(status_code=409, detail="Match was already reviewed")
    audit(db, "MANUAL_MATCH_REJECTED", None, "ReconciliationMatch", match.id, "Manual reconciliation match rejected", actor=match.reviewed_by); await db.commit(); return {"id": match.id, "status": match.status}


class SourceSettingPatch(BaseModel):
    enabled: bool | None = None
    in_daily_batch: bool | None = None
    manual_sync: bool | None = None
    sync_frequency: Literal["off", "daily", "weekly"] | None = None
    sync_time: str | None = None          # HH:MM, 24 hour
    sync_weekday: int | None = Field(default=None, ge=0, le=6)   # 0 = Monday
    sync_timezone: str | None = Field(default=None, max_length=64)

    @field_validator("sync_time")
    @classmethod
    def valid_time(cls, v):
        if v is not None and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v):
            raise ValueError("sync_time must be HH:MM in 24 hour time")
        return v

    @field_validator("sync_timezone")
    @classmethod
    def known_zone(cls, v):
        if v is not None:
            try:
                ZoneInfo(v)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise ValueError("sync_timezone must be an IANA time zone such as Europe/London") from exc
        return v


@router.get("/api/ingest/sources")
async def list_sources(db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("analyst", "it", "auditor"))):
    """Every source with its switches, when it last delivered, and how many records are in quarantine."""
    settings = await source_settings.load(db)
    last = dict((await db.execute(select(SourceRecord.source_system, func.max(SourceRecord.received_at))
                                  .group_by(SourceRecord.source_system))).all())
    held = dict((await db.execute(select(SourceRecord.source_system, func.count()).where(SourceRecord.status == "Quarantined")
                                  .group_by(SourceRecord.source_system))).all())
    now = utcnow()
    saved = {r.source_system: r for r in (await db.execute(select(SourceSetting))).scalars().all()}
    return [{"source_system": s, **settings[s], "pulled": s in source_settings.PULLED,
             "configured": source_settings.shopify_configured() if s == "Shopify" else None,
             "schedule": scheduler.describe(saved.get(s)) if s in source_settings.PULLED else None,
             "next_run_at": scheduler.next_run(saved.get(s), now) if s in source_settings.PULLED else None,
             "last_received_at": last.get(s), "quarantined": held.get(s, 0)} for s in source_settings.SOURCES]


@router.put("/api/ingest/sources/{source_system}")
async def update_source(source_system: str, body: SourceSettingPatch, db: AsyncSession = Depends(get_db),
                        user: dict = Depends(require_role("it"))):
    if source_system not in source_settings.SOURCES:
        raise HTTPException(status_code=404, detail=f"Unknown source. Choose one of {list(source_settings.SOURCES)}")
    row = (await db.execute(select(SourceSetting).where(SourceSetting.source_system == source_system))).scalar_one_or_none()
    if row is None:
        row = SourceSetting(source_system=source_system, **source_settings.effective(source_system, None))
        db.add(row)
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    schedule = {k: changes.pop(k) for k in ("sync_frequency", "sync_time", "sync_weekday", "sync_timezone") if k in changes}
    if schedule and source_system not in source_settings.PULLED:
        raise HTTPException(status_code=422, detail=f"{source_system} is delivered to Nimbus, so it has no sync schedule")
    notes = []
    for field, value in changes.items():
        setattr(row, field, value)
        notes.append(f"{field.replace('_', ' ')} {'on' if value else 'off'}")
    if schedule:
        frequency = schedule.get("sync_frequency")
        if frequency == "off":
            row.sync_frequency = row.sync_time = row.sync_weekday = None
        else:
            if frequency:
                row.sync_frequency = frequency
            row.sync_time = schedule.get("sync_time", row.sync_time)
            row.sync_weekday = schedule.get("sync_weekday", row.sync_weekday if row.sync_weekday is not None else 0)
            row.sync_timezone = schedule.get("sync_timezone", row.sync_timezone) or "UTC"
            if not row.sync_frequency or not row.sync_time:
                raise HTTPException(status_code=422, detail="A schedule needs a frequency (daily or weekly) and a time of day")
        row.last_scheduled_run_at = utcnow()   # a schedule counts from now; it never runs for moments already past
        row.last_scheduled_status = row.last_scheduled_message = None
        notes.append("schedule " + scheduler.describe(row).lower())
    actor = user.get("user") or "ontology.user"
    row.updated_by = actor
    if changes:
        audit(db, "SOURCE_SETTING_CHANGED", None, "SourceSetting", source_system, f"{source_system}: " + ", ".join(notes), actor=actor)
    await db.commit()
    return next(s for s in await list_sources(db, user) if s["source_system"] == source_system)


@router.post("/api/ingest/profiles")
async def create_source_profile(body: SourceFeedProfileRequest, db: AsyncSession = Depends(get_db),
                                _: dict = Depends(require_role("it"))):
    if body.source_system not in VALID_TYPES:
        raise HTTPException(status_code=422, detail=f"source_system must be one of {sorted(VALID_TYPES)}")
    profile = SourceFeedProfile(id=str(uuid.uuid4()), name=body.name.strip(), source_system=body.source_system,
                                schedule=body.schedule, column_mapping=body.column_mapping, enabled=body.enabled)
    db.add(profile)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if _is_duplicate_profile_name(exc):
            raise HTTPException(status_code=409, detail="A source-feed profile with this name already exists") from exc
        raise
    await db.refresh(profile)
    return profile_view(profile)


@router.patch("/api/ingest/profiles/{profile_id}")
async def update_source_profile(profile_id: str, body: SourceFeedProfilePatch, db: AsyncSession = Depends(get_db),
                                _: dict = Depends(require_role("it"))):
    profile = (await db.execute(select(SourceFeedProfile).where(SourceFeedProfile.id == profile_id))).scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="Source-feed profile not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)
    await db.commit()
    await db.refresh(profile)
    return profile_view(profile)


@router.get("/api/ingest/contracts")
async def ingest_contracts(_: dict = Depends(require_role("it", "finance"))):
    """Published input contracts for initial read-only source deliveries."""
    return {
        "transaction_columns": ["source_record_id", "event_time", "record_type", "amount", "store_id",
                                "currency", "payment_reference", "register_id", "cashier_id", "tender_type", "settled_on"],
        "pos_control_columns": ["source_record_id", "business_date", "store_id", "declared_count", "declared_total"],
        "sources": {source: sorted(types) for source, types in VALID_TYPES.items()},
        "note": "Use ISO-8601 timestamps, positive source amounts, canonical store IDs, and a unique delivery_id."}


@router.post("/api/ingest")
async def ingest_records(req: IngestRequest, db: AsyncSession = Depends(get_db),
                         _: dict = Depends(require_role("it"))):
    if req.source_system not in VALID_TYPES:
        raise HTTPException(status_code=400, detail=f"source_system must be one of {sorted(VALID_TYPES)}")
    await source_settings.require_allowed(db, req.source_system, manual=True)
    stats = await ingest.ingest(db, req.source_system, req.delivery_id, req.records)
    await db.commit()
    return stats


@router.post("/api/ingest/csv")
async def ingest_csv(req: CsvIngestRequest, db: AsyncSession = Depends(get_db),
                     _: dict = Depends(require_role("it"))):
    """Ingest a read-only source extract using the published CSV contract."""
    if req.source_system not in VALID_TYPES:
        raise HTTPException(status_code=400, detail=f"source_system must be one of {sorted(VALID_TYPES)}")
    await source_settings.require_allowed(db, req.source_system, manual=True)
    mapping = {}
    if req.profile_id:
        profile = (await db.execute(select(SourceFeedProfile).where(SourceFeedProfile.id == req.profile_id))).scalar_one_or_none()
        if not profile or not profile.enabled:
            raise HTTPException(status_code=404, detail="Enabled source-feed profile not found")
        if profile.source_system != req.source_system:
            raise HTTPException(status_code=422, detail="Profile source_system does not match delivery source_system")
        mapping = profile.column_mapping or {}
    try:
        rows = parse_csv_delivery(req.csv_data, mapping)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    stats = await ingest.ingest(db, req.source_system, req.delivery_id, rows)
    await db.commit()
    return {**stats, "delivery_id": req.delivery_id, "source_system": req.source_system}


@router.post("/api/ingest/simulate")
async def simulate_ingest(db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("it"))):
    earliest = (await db.execute(select(func.min(CanonicalTransaction.business_date)))).scalar()
    day = (datetime.fromisoformat(earliest) - timedelta(days=1)).strftime("%Y-%m-%d") if earliest else None
    feeds = simulate_feed(day)
    delivery = f"SIM-{datetime.now(timezone.utc).strftime('%H%M%S')}-{uuid.uuid4().hex[:4]}"
    result = {src: await ingest.ingest(db, src, delivery, recs) for src, recs in feeds.items()}
    await db.commit()
    return result


@router.post("/api/reconcile")
async def run_reconciliation(business_date: Annotated[str, Query(min_length=10, max_length=10)],
                             db: AsyncSession = Depends(get_db),
                             user: dict = Depends(require_role("it", "finance"))):
    try:
        validate_business_date(business_date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        result = await reconciliation.reconcile(db, business_date, store_ids=scope.allowed_stores(user))
    except reconciliation.ReconciliationCapacityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.commit()
    return result


@router.get("/api/ingest/stats")
async def ingest_stats(
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("analyst", "it", "auditor")),
):
    src = (await db.execute(select(SourceRecord.status, func.count()).group_by(SourceRecord.status))).all()
    txn = (await db.execute(select(CanonicalTransaction.reconciliation_status, func.count())
                            .group_by(CanonicalTransaction.reconciliation_status))).all()
    bad = (await db.execute(select(SourceRecord).where(SourceRecord.status == "Quarantined")
                            .order_by(SourceRecord.received_at.desc()).limit(10))).scalars().all()
    return {
        "source_records": {k: v for k, v in src},
        "transactions": {k: v for k, v in txn},
        "quarantined": [{"id": b.id, "source_system": b.source_system, "source_record_id": b.source_record_id,
                         "reason": b.quarantine_reason, "received_at": b.received_at} for b in bad],
    }
