import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated

from database import get_db
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from integrations.ontology.nimbus import audit
from model_base import utcnow
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.audit.models import AuditEvent
from apps.database_errors import violates_unique_constraint
from apps.ingest import auto_audit, reconciliation, register_events, source_settings
from apps.ingest import service as ingest
from apps.ingest.common import VALID_TYPES
from apps.ingest.contracts import (
    CsvIngestRequest,
    IngestRequest,
    ManualMatchRequest,
    PosLogRequest,
    ReconciliationPolicyRequest,
    SourceFeedProfilePatch,
    SourceFeedProfileRequest,
    SourceSettingPatch,
    json_payload_size,
    parse_csv_delivery,
    schedule_window_hours,
    validate_profile_mapping,
)
from apps.ingest.deliveries import delivery_history
from apps.ingest.deliveries import router as deliveries_router
from apps.ingest.manifests import validate_business_date
from apps.ingest.manual_matches import (
    approve_manual_match as approve_manual_match,
)
from apps.ingest.manual_matches import (
    authorized_match_transactions as authorized_match_transactions,
)
from apps.ingest.manual_matches import (
    list_manual_matches as list_manual_matches,
)
from apps.ingest.manual_matches import (
    propose_manual_match as propose_manual_match,
)
from apps.ingest.manual_matches import (
    reject_manual_match as reject_manual_match,
)
from apps.ingest.manual_matches import (
    router as manual_matches_router,
)
from apps.ingest.models import (
    ReconciliationPolicy,
    SourceFeedProfile,
    SourceRecord,
    SourceSetting,
)
from apps.ingest.policy_packages import router as policy_packages_router
from apps.ingest.pos_formats import profile as pos_profile
from apps.ingest.pos_formats import rtlog, standard
from apps.ingest.profiles import (
    _is_duplicate_profile_name,
    create_source_profile,
    feed_health,
    list_source_profiles,
    profile_view,
    update_source_profile,
)
from apps.ingest.profiles import (
    router as profiles_router,
)
from apps.ingest.shopify import scheduler
from apps.ingest.simulation import simulate_feed
from apps.security import scope
from apps.security.authentication import require_role
from apps.store_days.freshness import invalidate_configuration
from apps.totals.currency import validate_currency
from apps.transactions.models import CanonicalTransaction

__all__ = [
    "ManualMatchRequest",
    "SourceFeedProfilePatch",
    "SourceFeedProfileRequest",
    "_is_duplicate_profile_name",
    "create_source_profile",
    "delivery_history",
    "feed_health",
    "json_payload_size",
    "list_source_profiles",
    "profile_view",
    "schedule_window_hours",
    "validate_profile_mapping",
    "update_source_profile",
]

router = APIRouter()
logger = logging.getLogger(__name__)
router.include_router(manual_matches_router)
router.include_router(deliveries_router)
router.include_router(policy_packages_router)


def _is_active_reconciliation_policy_conflict(exc: IntegrityError) -> bool:
    return violates_unique_constraint(
        exc,
        constraint_names={"uq_reconciliation_policies_one_active"},
        sqlite_messages={"UNIQUE constraint failed: reconciliation_policies.active"},
    )


def _reconciliation_policy_state(policy: ReconciliationPolicy | None) -> dict | None:
    if policy is None:
        return None
    return {
        "amount_tolerance": str(policy.amount_tolerance),
        "amount_tolerance_currency": policy.amount_tolerance_currency,
        "settlement_day_tolerance": policy.settlement_day_tolerance,
        "duplicate_lookback_days": policy.duplicate_lookback_days,
        "gap_min_count": policy.gap_min_count,
        "gap_share": str(policy.gap_share),
    }


def _audit_reconciliation_policy_change(
    *, policy: ReconciliationPolicy, before: dict | None, actor: str, reason: str
) -> AuditEvent:
    return AuditEvent(
        id=str(uuid.uuid4()),
        event_type="RECONCILIATION_POLICY_CHANGED",
        actor=actor,
        object_type="ReconciliationPolicy",
        object_id=policy.id,
        action_description=reason,
        before_state=before,
        after_state=_reconciliation_policy_state(policy),
    )


@router.get("/api/reconciliation/policy")
async def get_reconciliation_policy(
    db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("finance", "it"))
):
    policy = (
        await db.execute(
            select(ReconciliationPolicy)
            .where(ReconciliationPolicy.active.is_(True))
            .order_by(ReconciliationPolicy.updated_at.desc(), ReconciliationPolicy.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return {
        "source": "local-fallback",
        "amount_tolerance": policy.amount_tolerance if policy else 0,
        "amount_tolerance_currency": policy.amount_tolerance_currency if policy else "USD",
        "settlement_day_tolerance": policy.settlement_day_tolerance if policy else 0,
        "duplicate_lookback_days": policy.duplicate_lookback_days if policy else 0,
        "gap_min_count": policy.gap_min_count if policy else 10,
        "gap_share": policy.gap_share if policy else 0.1,
        "updated_by": policy.updated_by if policy else None,
        "updated_at": policy.updated_at if policy else None,
    }


@router.put("/api/reconciliation/policy")
async def update_reconciliation_policy(
    body: ReconciliationPolicyRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance")),
):
    try:
        validate_currency(body.amount_tolerance_currency)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if (
        not body.amount_tolerance.is_finite()
        or body.amount_tolerance < 0
        or body.amount_tolerance > Decimal("9999999999999.99")
        or body.amount_tolerance != body.amount_tolerance.quantize(Decimal("0.01"))
        or body.settlement_day_tolerance < 0
        or body.settlement_day_tolerance > 31
        or body.duplicate_lookback_days < 0
        or body.duplicate_lookback_days > 365
    ):
        raise HTTPException(
            status_code=422,
            detail="Tolerance values must be non-negative; settlement days may not exceed 31 and duplicate lookback may not exceed 365",
        )
    if (
        not body.gap_share.is_finite()
        or body.gap_min_count < 1
        or not 0 < body.gap_share <= 1
        or body.gap_share != body.gap_share.quantize(Decimal("0.0001"))
    ):
        raise HTTPException(
            status_code=422, detail="Gap minimum must be at least 1 and gap share between 0 and 1"
        )
    policy_query = (
        select(ReconciliationPolicy)
        .where(ReconciliationPolicy.active.is_(True))
        .order_by(ReconciliationPolicy.updated_at.desc(), ReconciliationPolicy.id.desc())
        .limit(1)
        .with_for_update()
    )
    policy = (await db.execute(policy_query)).scalar_one_or_none()
    before_state = _reconciliation_policy_state(policy)
    if not policy:
        policy = ReconciliationPolicy(
            id=str(uuid.uuid4()), name="Nimbus fallback reconciliation policy"
        )
        db.add(policy)
    policy.amount_tolerance, policy.settlement_day_tolerance = (
        body.amount_tolerance,
        body.settlement_day_tolerance,
    )
    policy.duplicate_lookback_days = body.duplicate_lookback_days
    policy.amount_tolerance_currency = body.amount_tolerance_currency
    policy.gap_min_count, policy.gap_share = body.gap_min_count, Decimal(str(body.gap_share))
    policy.updated_by, policy.updated_at = user["user"] or "ontology.user", utcnow()
    db.add(
        _audit_reconciliation_policy_change(
            policy=policy,
            before=before_state,
            actor=user["user"] or "ontology.user",
            reason=body.reason,
        )
    )
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
            raise HTTPException(
                status_code=409, detail="Reconciliation policy changed concurrently"
            ) from exc
        before_state = _reconciliation_policy_state(policy)
        policy.amount_tolerance, policy.settlement_day_tolerance = (
            body.amount_tolerance,
            body.settlement_day_tolerance,
        )
        policy.duplicate_lookback_days = body.duplicate_lookback_days
        policy.amount_tolerance_currency = body.amount_tolerance_currency
        policy.gap_min_count, policy.gap_share = body.gap_min_count, Decimal(str(body.gap_share))
        policy.updated_by, policy.updated_at = user["user"] or "ontology.user", utcnow()
        db.add(
            _audit_reconciliation_policy_change(
                policy=policy,
                before=before_state,
                actor=user["user"] or "ontology.user",
                reason=body.reason,
            )
        )
        await db.commit()
    return {
        "source": "local-fallback",
        "amount_tolerance": policy.amount_tolerance,
        "amount_tolerance_currency": policy.amount_tolerance_currency,
        "settlement_day_tolerance": policy.settlement_day_tolerance,
        "duplicate_lookback_days": policy.duplicate_lookback_days,
        "gap_min_count": policy.gap_min_count,
        "gap_share": policy.gap_share,
        "updated_by": policy.updated_by,
        "updated_at": policy.updated_at,
    }


@router.get("/api/ingest/sources")
async def list_sources(
    db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("analyst", "it", "auditor"))
):
    """Every source with its switches, when it last delivered, and how many records are in quarantine."""
    settings = await source_settings.load(db)
    last = dict(
        (
            await db.execute(
                select(SourceRecord.source_system, func.max(SourceRecord.received_at)).group_by(
                    SourceRecord.source_system
                )
            )
        ).all()
    )
    held = dict(
        (
            await db.execute(
                select(SourceRecord.source_system, func.count())
                .where(SourceRecord.status == "Quarantined")
                .group_by(SourceRecord.source_system)
            )
        ).all()
    )
    now = utcnow()
    saved = {r.source_system: r for r in (await db.execute(select(SourceSetting))).scalars().all()}
    return [
        {
            "source_system": s,
            **settings[s],
            "pulled": s in source_settings.PULLED,
            "configured": source_settings.shopify_configured() if s == "Shopify" else None,
            "schedule": scheduler.describe(saved.get(s)) if s in source_settings.PULLED else None,
            "next_run_at": scheduler.next_run(saved.get(s), now)
            if s in source_settings.PULLED
            else None,
            "last_received_at": last.get(s),
            "quarantined": held.get(s, 0),
        }
        for s in source_settings.SOURCES
    ]


@router.put("/api/ingest/sources/{source_system}")
async def update_source(
    source_system: str,
    body: SourceSettingPatch,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("it")),
):
    if source_system not in source_settings.SOURCES:
        raise HTTPException(
            status_code=404, detail=f"Unknown source. Choose one of {list(source_settings.SOURCES)}"
        )
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if source_system in {"OMS", "OMSControl"} and changes.get("in_daily_batch") is True:
        raise HTTPException(
            status_code=422,
            detail="OMS nightly batch is unavailable until a retailer-specific OMS connector is configured",
        )
    row = await source_settings.lock_source_settings(db, source_system)
    previous_day_close_requirement = row.require_day_close if row is not None else False
    if row is None:
        row = SourceSetting(
            source_system=source_system, **source_settings.effective(source_system, None)
        )
        db.add(row)
    if "require_day_close" in changes and source_system != "POS":
        raise HTTPException(
            status_code=422, detail="The Day Close requirement applies to the POS source"
        )
    schedule = {
        k: changes.pop(k)
        for k in ("sync_frequency", "sync_time", "sync_weekday", "sync_timezone")
        if k in changes
    }
    if schedule and source_system not in source_settings.PULLED:
        raise HTTPException(
            status_code=422,
            detail=f"{source_system} is delivered to Nimbus, so it has no sync schedule",
        )
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
            row.sync_weekday = schedule.get(
                "sync_weekday", row.sync_weekday if row.sync_weekday is not None else 0
            )
            row.sync_timezone = schedule.get("sync_timezone", row.sync_timezone) or "UTC"
            if not row.sync_frequency or not row.sync_time:
                raise HTTPException(
                    status_code=422,
                    detail="A schedule needs a frequency (daily or weekly) and a time of day",
                )
        row.last_scheduled_run_at = (
            utcnow()
        )  # a schedule counts from now; it never runs for moments already past
        row.last_scheduled_status = row.last_scheduled_message = None
        notes.append("schedule " + scheduler.describe(row).lower())
    actor = user.get("user") or "ontology.user"
    row.updated_by = actor
    if changes or schedule:
        audit(
            db,
            "SOURCE_SETTING_CHANGED",
            None,
            "SourceSetting",
            source_system,
            f"{source_system}: " + ", ".join(notes),
            actor=actor,
        )
    if source_system == "POS" and row.require_day_close != previous_day_close_requirement:
        await invalidate_configuration(
            db,
            actor=actor,
            reason="POS Day Close requirement changed",
        )
    await db.commit()
    return next(s for s in await list_sources(db, user) if s["source_system"] == source_system)


@router.get("/api/ingest/contracts")
async def ingest_contracts(_: dict = Depends(require_role("it", "finance"))):
    """Published input contracts for initial read-only source deliveries."""
    return {
        "transaction_columns": [
            "source_record_id",
            "event_time",
            "record_type",
            "amount",
            "store_id",
            "business_date",
            "currency",
            "payment_reference",
            "register_id",
            "cashier_id",
            "tender_type",
            "settled_on",
        ],
        "pos_control_columns": [
            "source_record_id",
            "business_date",
            "store_id",
            "declared_count",
            "declared_total",
        ],
        "sources": {source: sorted(types) for source, types in VALID_TYPES.items()},
        "note": "Use ISO-8601 timestamps, positive source amounts, canonical store IDs, a unique delivery_id, and pass the source-assigned business_date for overnight store days.",
    }


@router.post("/api/ingest")
async def ingest_records(
    req: IngestRequest,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("it")),
    request: Request = None,
):
    if req.source_system not in VALID_TYPES:
        raise HTTPException(
            status_code=400, detail=f"source_system must be one of {sorted(VALID_TYPES)}"
        )
    await source_settings.require_allowed(db, req.source_system, manual=True)
    stats = await ingest.ingest(db, req.source_system, req.delivery_id, req.records)
    await db.commit()
    automatic_audit = await auto_audit.reconcile_after_import(
        db, req.source_system, req.records, request=request
    )
    return {**stats, "automatic_audit": automatic_audit}


@router.post("/api/ingest/pos-log")
async def ingest_pos_log(
    req: PosLogRequest,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("it")),
    request: Request = None,
):
    """Ingest a POS system's log file: sales and returns as POS transactions, the rest as register events."""
    try:
        validate_currency(req.currency)
        log = rtlog.parse_rtlog(req.content, name=req.delivery_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    mapped, left_over = standard.ingest_records(log, req.currency)
    unmapped = standard.unmapped_merchandise(left_over)
    blockers = list(log.problems)
    if unmapped:
        blockers.append(
            f"{len(unmapped)} merchandise transaction(s) have no usable item lines and cannot be ingested"
        )
    if blockers:
        raise HTTPException(
            status_code=422,
            detail="RTLOG was not ingested: " + "; ".join(blockers[:10]),
        )
    await source_settings.require_allowed(db, "POS", manual=True)
    stats = await ingest.ingest(db, "POS", req.delivery_id, mapped)
    events = await register_events.ingest_register_events(db, log, req.currency)
    await db.commit()
    automatic_audit = await auto_audit.reconcile_after_import(
        db,
        "POS",
        mapped,
        request=request,
        extra_scopes={(log.business_date, log.store_id)},
    )
    return {
        "format": req.format,
        "source_system": "POS",
        "delivery_id": req.delivery_id,
        "store_id": log.store_id,
        "business_date": log.business_date,
        "transactions": len(log.transactions),
        "sales_and_returns": stats,
        "automatic_audit": automatic_audit,
        "register_events": events,
        "not_mapped": [
            {"transaction_number": tx["transaction_number"], "type": tx["type"]}
            for tx in unmapped
        ],
        "problems": log.problems,
    }


@router.post("/api/ingest/pos-log/profile")
async def profile_pos_log(
    req: PosLogRequest,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("it", "analyst", "auditor")),
):
    """Describe what a POS log contains and which audit rules it supports, without ingesting anything."""
    try:
        validate_currency(req.currency)
        log = rtlog.parse_rtlog(req.content, name=req.delivery_id)
        mapped, left_over = standard.ingest_records(log, req.currency)
        unmapped = standard.unmapped_merchandise(left_over)
        blockers = list(log.problems)
        if unmapped:
            blockers.append(
                f"{len(unmapped)} merchandise transaction(s) have no usable item lines and cannot be ingested"
            )
        errors = []
        valid = 0
        max_age_days = await ingest.max_backdated_transaction_days(db)
        today = utcnow().date()
        for row_number, record in enumerate(mapped, start=1):
            prepared = ingest.prepare_record("POS", record)
            reason = prepared.parse_error
            if prepared.parsed and max_age_days is not None:
                event_date = prepared.parsed["ts"].astimezone(UTC).date()
                age_days = (today - event_date).days
                if age_days > max_age_days:
                    reason = (
                        f"Transaction is {age_days} days old; configured maximum backdated "
                        f"age is {max_age_days} days"
                    )
            if reason:
                if len(errors) < 10:
                    errors.append({"record": row_number, "reason": reason})
            else:
                valid += 1
        return {
            **pos_profile.profile_rtlog(req.content, name=req.delivery_id),
            "ingestable_records": len(mapped),
            "register_events": len(left_over) - len(unmapped),
            "valid_records": valid,
            "quarantined_records": len(mapped) - valid,
            "not_mapped": len(unmapped),
            "ingest_blockers": blockers,
            "validation_errors": errors,
            "validation_errors_truncated": len(mapped) - valid > len(errors),
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/api/ingest/csv")
async def ingest_csv(
    req: CsvIngestRequest,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("it")),
    request: Request = None,
):
    """Ingest a read-only source extract using the published CSV contract."""
    if req.source_system not in VALID_TYPES:
        raise HTTPException(
            status_code=400, detail=f"source_system must be one of {sorted(VALID_TYPES)}"
        )
    await source_settings.require_allowed(db, req.source_system, manual=True)
    mapping = {}
    if req.profile_id:
        profile = (
            await db.execute(
                select(SourceFeedProfile).where(SourceFeedProfile.id == req.profile_id)
            )
        ).scalar_one_or_none()
        if not profile or not profile.enabled:
            raise HTTPException(status_code=404, detail="Enabled source-feed profile not found")
        if profile.source_system != req.source_system:
            raise HTTPException(
                status_code=422,
                detail="Profile source_system does not match delivery source_system",
            )
        mapping = profile.column_mapping or {}
    try:
        rows = parse_csv_delivery(req.csv_data, mapping)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    stats = await ingest.ingest(db, req.source_system, req.delivery_id, rows)
    await db.commit()
    automatic_audit = await auto_audit.reconcile_after_import(
        db, req.source_system, rows, request=request
    )
    return {
        **stats,
        "delivery_id": req.delivery_id,
        "source_system": req.source_system,
        "automatic_audit": automatic_audit,
    }


@router.post("/api/ingest/csv/preview")
async def preview_csv(
    req: CsvIngestRequest,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("it")),
):
    """Validate a CSV delivery without archiving records or changing business data."""
    if req.source_system not in VALID_TYPES:
        raise HTTPException(
            status_code=400, detail=f"source_system must be one of {sorted(VALID_TYPES)}"
        )
    await source_settings.require_allowed(db, req.source_system, manual=True)
    mapping = {}
    if req.profile_id:
        profile = (
            await db.execute(
                select(SourceFeedProfile).where(SourceFeedProfile.id == req.profile_id)
            )
        ).scalar_one_or_none()
        if not profile or not profile.enabled:
            raise HTTPException(status_code=404, detail="Enabled source-feed profile not found")
        if profile.source_system != req.source_system:
            raise HTTPException(
                status_code=422,
                detail="Profile source_system does not match delivery source_system",
            )
        mapping = profile.column_mapping or {}
    try:
        rows = parse_csv_delivery(req.csv_data, mapping)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    errors = []
    stores, dates = set(), set()
    valid = 0
    max_age_days = (
        await ingest.max_backdated_transaction_days(db) if req.source_system == "POS" else None
    )
    today = utcnow().date()
    for row_number, row in enumerate(rows, start=2):
        prepared = ingest.prepare_record(req.source_system, row)
        reason = prepared.parse_error
        if prepared.parsed and not prepared.parsed.get("manifest") and max_age_days is not None:
            event_date = prepared.parsed["ts"].astimezone(UTC).date()
            age_days = (today - event_date).days
            if age_days > max_age_days:
                reason = (
                    f"Transaction is {age_days} days old; configured maximum backdated "
                    f"age is {max_age_days} days"
                )
        if reason:
            if len(errors) < 10:
                errors.append({"row": row_number, "reason": reason})
            continue
        valid += 1
        stores.add(row.get("store_id"))
        parsed = prepared.parsed
        dates.add(parsed.get("business_date") or parsed["ts"].date().isoformat())
    return {
        "source_system": req.source_system,
        "records_received": len(rows),
        "valid": valid,
        "quarantined": len(rows) - valid,
        "errors": errors,
        "errors_truncated": len(rows) - valid > len(errors),
        "stores": sorted(store for store in stores if isinstance(store, str)),
        "business_dates": sorted(dates),
    }


@router.post("/api/ingest/simulate")
async def simulate_ingest(
    db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("it"))
):
    earliest = (await db.execute(select(func.min(CanonicalTransaction.business_date)))).scalar()
    day = (
        (datetime.fromisoformat(earliest) - timedelta(days=1)).strftime("%Y-%m-%d")
        if earliest
        else None
    )
    feeds = simulate_feed(day)
    delivery = f"SIM-{datetime.now(UTC).strftime('%H%M%S')}-{uuid.uuid4().hex[:4]}"
    result = {src: await ingest.ingest(db, src, delivery, recs) for src, recs in feeds.items()}
    await db.commit()
    return result


@router.post("/api/reconcile")
async def run_reconciliation(
    business_date: Annotated[str, Query(min_length=10, max_length=10)],
    store_id: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
    store_ids: Annotated[list[str] | None, Query(max_length=500)] = None,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("it", "finance", "admin")),
    request: Request = None,
):
    try:
        validate_business_date(business_date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if store_id is not None and store_ids is not None:
        raise HTTPException(status_code=422, detail="Use store_id or store_ids, not both")
    allowed = scope.allowed_stores(user)
    if store_id is not None:
        scope.require_store(user, store_id)
        selected_stores = {store_id}
    elif store_ids is not None:
        if not store_ids or any(
            not value.strip() or len(value.strip()) > 50 for value in store_ids
        ):
            raise HTTPException(status_code=422, detail="store_ids must contain valid store IDs")
        selected_stores = {value.strip() for value in store_ids}
        for selected_store in selected_stores:
            scope.require_store(user, selected_store)
    else:
        selected_stores = set(allowed) if allowed is not None else None
    try:
        result = await auto_audit.reconcile_store_scope(
            db, business_date, selected_stores, request=request
        )
    except reconciliation.ReconciliationCapacityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return result


@router.post("/api/reconcile/refresh-errors")
async def refresh_audit_errors(
    business_date: Annotated[str, Query(min_length=10, max_length=10)],
    store_id: Annotated[str, Query(min_length=1, max_length=50)],
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("it", "finance", "admin")),
):
    """Re-run reconciliation rules for one store day without automation or export handoffs."""
    try:
        validate_business_date(business_date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    scope.require_store(user, store_id)
    try:
        result = await reconciliation.reconcile(db, business_date, store_ids={store_id})
    except reconciliation.ReconciliationCapacityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.commit()
    return result


@router.get("/api/ingest/stats")
async def ingest_stats(
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("analyst", "it", "auditor")),
):
    src = (
        await db.execute(select(SourceRecord.status, func.count()).group_by(SourceRecord.status))
    ).all()
    txn = (
        await db.execute(
            select(CanonicalTransaction.reconciliation_status, func.count()).group_by(
                CanonicalTransaction.reconciliation_status
            )
        )
    ).all()
    return {
        "source_records": {k: v for k, v in src},
        "transactions": {k: v for k, v in txn},
    }


router.include_router(profiles_router)
