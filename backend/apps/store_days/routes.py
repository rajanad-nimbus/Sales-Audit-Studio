"""Store-day lifecycle and versioned sales-audit totals."""
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select, union
from sqlalchemy.ext.asyncio import AsyncSession

from apps.cases.identifiers import generate_case_number
from apps.ingest import reconciliation
from apps.ingest.manifests import iter_manifests, validate_business_date
from apps.security import scope
from apps.store_days.freshness import lock_configuration, lock_store_day_identity
from apps.totals.calculation import calculate_store_day, effective_definitions
from apps.totals.currency import single_currency
from apps.security.authentication import require_role
from database import get_db
from apps.audit.models import AuditEvent
from apps.transactions.models import CanonicalTransaction
from apps.cases.models import (
    Case,
    EvidenceRequest,
    Exception as DBException,
)
from model_base import utcnow
from apps.controls.foundation.models import FoundationReference
from apps.controls.rules.models import ConfiguredAuditRule
from apps.store_days.models import AuditTotal, StoreDay, StoreDayClosureRequest
from integrations.ontology.nimbus import audit, sla_due

router = APIRouter(prefix="/api/store-days", tags=["Store days"])
REQUIRED_COMPARISON_TOTALS = {"Processor to Bank Variance", "POS to GL Variance"}
OPTIONAL_COMPARISON_TOTALS = {"POS Control Declared"}
MAX_STORE_DAY_HISTORY_ITEMS = 200


def comparison_blockers(day: StoreDay, totals: list[AuditTotal]) -> list[str]:
    comparisons = [total for total in totals if total.level == "Store" and
                   total.total_name in REQUIRED_COMPARISON_TOTALS | OPTIONAL_COMPARISON_TOTALS]
    present = {total.total_name for total in comparisons}
    blockers = []
    if day.totals_stale:
        blockers.append("totals are stale; re-total the store day")
    if day.audit_version < 1 or not REQUIRED_COMPARISON_TOTALS <= present or any(
        total.variance_amount is None for total in comparisons
    ):
        blockers.append("current reconciliation totals are missing")
    if any(total.variance_amount is not None and total.variance_amount != 0 for total in comparisons):
        blockers.append("unresolved reconciliation variance")
    return blockers


class OpenStoreDayBody(BaseModel):
    store_id: str = Field(min_length=1, max_length=50)
    business_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


async def open_store_day_record(db: AsyncSession, store_id: str, business_date: str, actor: str) -> StoreDay:
    """Create the controlled audit unit for one store and business date. Does not commit.

    There is no scheduler yet, so an IT administrator opens each day. Re-total then calculates against it.
    """
    await lock_configuration(db)
    store_id = store_id.strip()
    if not store_id or len(store_id) > 50:
        raise HTTPException(status_code=422, detail="store_id must contain 1 to 50 non-whitespace characters")
    try:
        validate_business_date(business_date)
        date = datetime.fromisoformat(business_date).date()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if date > datetime.now(timezone.utc).date():
        raise HTTPException(status_code=422, detail="A Store Day cannot be opened for a future date")
    await lock_store_day_identity(db, store_id, business_date)
    known = (await db.execute(select(FoundationReference.id).where(
        FoundationReference.category == "Store", FoundationReference.code == store_id,
        FoundationReference.active == True).limit(1))).scalar_one_or_none()  # noqa: E712
    if not known:
        known = (await db.execute(select(CanonicalTransaction.id).where(CanonicalTransaction.store_id == store_id).limit(1))).scalar_one_or_none()
    if not known:
        raise HTTPException(status_code=422, detail=f"Unknown store {store_id}. Add it to the store master first.")
    if (await db.execute(select(StoreDay.id).where(StoreDay.store_id == store_id, StoreDay.business_date == business_date))).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="That Store Day is already open")
    day = StoreDay(id=str(uuid.uuid4()), store_id=store_id, business_date=business_date, status="Open")
    db.add(day)
    audit(db, "STORE_DAY_OPENED", None, "StoreDay", day.id, f"Store day {store_id}/{business_date} opened", actor=actor)
    await db.flush()
    return day


@router.post("")
async def open_store_day(body: OpenStoreDayBody, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("it"))):
    day = await open_store_day_record(db, body.store_id, body.business_date, user["user"] or "ontology.user")
    await db.commit(); await db.refresh(day)
    return store_day_view(day)


class ReopenRequest(BaseModel):
    rationale: str = Field(min_length=1, max_length=2000)

class ClosureRequestBody(BaseModel):
    evidence_basis: str = Field(min_length=1, max_length=5000)

class ClosureReviewBody(BaseModel):
    rationale: str = Field(min_length=1, max_length=2000)


def total_view(total: AuditTotal) -> dict:
    return {"id": total.id, "version": total.audit_version, "name": total.total_name,
            "definition_id": total.definition_id, "definition_revision": total.definition_revision,
            "level": total.level, "dimension": total.dimension_key,
            "calculated_amount": total.calculated_amount, "declared_amount": total.declared_amount,
            "variance_amount": total.variance_amount, "currency": total.currency}


def store_day_view(day: StoreDay) -> dict:
    return {"id": day.id, "store_id": day.store_id, "business_date": day.business_date,
            "status": day.status, "audit_version": day.audit_version, "closed_at": day.closed_at,
            "totals_stale": day.totals_stale,
            "closed_by": day.closed_by, "reopened_by": day.reopened_by,
            "reopened_reason": day.reopened_reason, "updated_at": day.updated_at}


async def get_day(db: AsyncSession, store_id: str, business_date: str, *, lock: bool = False) -> StoreDay:
    if not store_id or len(store_id) > 50 or store_id != store_id.strip():
        raise HTTPException(status_code=422, detail="store_id must contain 1 to 50 characters without surrounding whitespace")
    try:
        validate_business_date(business_date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    query = select(StoreDay).where(StoreDay.store_id == store_id, StoreDay.business_date == business_date)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    day = (await db.execute(query)).scalar_one_or_none()
    if not day:
        raise HTTPException(status_code=404, detail="Store day not found; an IT administrator must open it first")
    return day


@router.get("")
async def list_store_days(store_id: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
                          limit: Annotated[int, Query(ge=1, le=500)] = 100,
                          db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance", "it"))):
    query = scope.restrict(select(StoreDay), user, StoreDay.store_id).order_by(StoreDay.business_date.desc(), StoreDay.store_id).limit(min(limit, 500))
    if store_id:
        query = query.where(StoreDay.store_id == store_id)
    rows = (await db.execute(query)).scalars().all()
    return [store_day_view(row) for row in rows]


@router.get("/stores")
async def store_selector(db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance", "it"))):
    if scope.scoped(user):
        return sorted(scope.allowed_stores(user) or [])
    stores = union(
        select(CanonicalTransaction.store_id.label("store_id")).where(
            CanonicalTransaction.store_id.is_not(None),
        ),
        select(StoreDay.store_id.label("store_id")).where(StoreDay.store_id.is_not(None)),
    ).subquery()
    return (await db.execute(select(stores.c.store_id).order_by(stores.c.store_id))).scalars().all()


@router.get("/{store_id}/{business_date}")
async def get_store_day(store_id: str, business_date: str,
                        version: Annotated[int | None, Query(ge=1)] = None,
                        db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance", "it"))):
    scope.require_store(user, store_id)
    day = await get_day(db, store_id, business_date)
    selected_version = version if version is not None else day.audit_version
    totals = (await db.execute(select(AuditTotal).where(AuditTotal.store_day_id == day.id,
                                                        AuditTotal.audit_version == selected_version)
                               .order_by(AuditTotal.level, AuditTotal.total_name, AuditTotal.dimension_key))).scalars().all()
    versions = (await db.execute(select(AuditTotal.audit_version).where(AuditTotal.store_day_id == day.id)
                                 .distinct().order_by(AuditTotal.audit_version.desc())
                                 .limit(MAX_STORE_DAY_HISTORY_ITEMS + 1))).scalars().all()
    versions_truncated = len(versions) > MAX_STORE_DAY_HISTORY_ITEMS
    versions = versions[:MAX_STORE_DAY_HISTORY_ITEMS]
    case_query = select(Case).where(Case.store_id == store_id, Case.business_date == business_date)
    case_counts = (
        await db.execute(
            select(
                func.count(Case.id),
                func.coalesce(
                    func.sum(case((Case.status != "Closed", 1), else_=0)),
                    0,
                ),
            ).where(Case.store_id == store_id, Case.business_date == business_date)
        )
    ).one()
    case_count, open_case_count = case_counts
    cases = (await db.execute(case_query.order_by(Case.created_at.desc(), Case.id.desc())
                              .limit(MAX_STORE_DAY_HISTORY_ITEMS + 1))).scalars().all()
    cases_truncated = len(cases) > MAX_STORE_DAY_HISTORY_ITEMS
    cases = cases[:MAX_STORE_DAY_HISTORY_ITEMS]
    blockers = []
    if open_case_count: blockers.append(f"{open_case_count} open case(s)")
    current_totals = totals if selected_version == day.audit_version else (
        await db.execute(select(AuditTotal).where(AuditTotal.store_day_id == day.id,
                                                  AuditTotal.audit_version == day.audit_version))
    ).scalars().all()
    blockers.extend(comparison_blockers(day, current_totals))
    requests = (await db.execute(select(StoreDayClosureRequest).where(StoreDayClosureRequest.store_day_id == day.id)
                                 .order_by(StoreDayClosureRequest.created_at.desc(), StoreDayClosureRequest.id.desc())
                                 .limit(MAX_STORE_DAY_HISTORY_ITEMS + 1))).scalars().all()
    requests_truncated = len(requests) > MAX_STORE_DAY_HISTORY_ITEMS
    requests = requests[:MAX_STORE_DAY_HISTORY_ITEMS]
    history = (await db.execute(select(AuditEvent).where(AuditEvent.object_type == "StoreDay", AuditEvent.object_id == day.id).order_by(AuditEvent.seq.desc()).limit(100))).scalars().all()
    # Cases closed over an evidence gap leave the open-case blocker, so list them here to keep them visible.
    overrides = (await db.execute(
        select(EvidenceRequest, Case).join(Case, Case.id == EvidenceRequest.case_id)
        .where(Case.store_id == store_id, Case.business_date == business_date,
               EvidenceRequest.status.in_(["Overridden", "Override Pending"]))
        .order_by(EvidenceRequest.created_at.desc(), EvidenceRequest.id.desc())
        .limit(MAX_STORE_DAY_HISTORY_ITEMS + 1))).all()
    overrides_truncated = len(overrides) > MAX_STORE_DAY_HISTORY_ITEMS
    overrides = overrides[:MAX_STORE_DAY_HISTORY_ITEMS]
    evidence_overrides = [{"case_id": c.id, "case_number": c.case_number, "requirement": r.requirement, "status": r.status,
                           "reason": r.override_reason, "financial_impact": str(r.override_financial_impact),
                           "requested_by": r.override_requested_by, "approved_by": r.override_approved_by} for r, c in overrides]
    return {"store_day": store_day_view(day), "selected_version": selected_version, "versions": versions,
            "versions_truncated": versions_truncated, "case_count": case_count,
            "cases_truncated": cases_truncated, "closure_requests_truncated": requests_truncated,
            "evidence_overrides_truncated": overrides_truncated,
            "close_blockers": blockers, "evidence_overrides": evidence_overrides, "totals": [total_view(total) for total in totals],
            "cases": [{"id": case.id, "case_number": case.case_number, "case_type": case.case_type,
                       "status": case.status, "exposure": case.total_exposure} for case in cases],
            "closure_requests": [{"id": item.id, "requested_by": item.requested_by, "evidence_basis": item.evidence_basis, "status": item.status, "reviewed_by": item.reviewed_by, "review_reason": item.review_reason, "created_at": item.created_at} for item in requests],
            "history": [{"id": event.id, "event_type": event.event_type, "actor": event.actor, "description": event.action_description, "created_at": event.created_at} for event in history]}

@router.post("/{store_id}/{business_date}/closure-requests")
async def request_store_day_closure(store_id: str, business_date: str, body: ClosureRequestBody, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance", "it"))):
    scope.require_store(user, store_id)
    if not body.evidence_basis.strip(): raise HTTPException(status_code=422, detail="Closure evidence basis is required")
    day = await get_day(db, store_id, business_date, lock=True)
    if day.status == "Closed": raise HTTPException(status_code=409, detail="Store day is already closed")
    pending = (await db.execute(select(StoreDayClosureRequest.id).where(StoreDayClosureRequest.store_day_id == day.id, StoreDayClosureRequest.status == "Pending"))).scalars().first()
    if pending: raise HTTPException(status_code=409, detail="A closure request is already pending Finance review")
    request = StoreDayClosureRequest(id=str(uuid.uuid4()), store_day_id=day.id, requested_by=user["user"] or "ontology.user", evidence_basis=body.evidence_basis.strip())
    db.add(request); audit(db, "STORE_DAY_CLOSURE_REQUESTED", None, "StoreDay", day.id, "Store day closure requested", actor=request.requested_by)
    await db.commit(); return {"id": request.id, "status": request.status}

@router.post("/{store_id}/{business_date}/closure-requests/{request_id}/reject")
async def reject_store_day_closure(store_id: str, business_date: str, request_id: str, body: ClosureReviewBody, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance"))):
    scope.require_store(user, store_id)
    if not body.rationale.strip(): raise HTTPException(status_code=422, detail="A rejection rationale is required")
    day = await get_day(db, store_id, business_date, lock=True)
    request = (await db.execute(select(StoreDayClosureRequest).where(StoreDayClosureRequest.id == request_id, StoreDayClosureRequest.store_day_id == day.id))).scalar_one_or_none()
    if not request or request.status != "Pending": raise HTTPException(status_code=409, detail="Pending closure request not found")
    request.status, request.reviewed_by, request.reviewed_at, request.review_reason = "Rejected", user["user"] or "ontology.user", utcnow(), body.rationale.strip()
    audit(db, "STORE_DAY_CLOSURE_REJECTED", None, "StoreDay", day.id, f"Closure request rejected: {request.review_reason}", actor=request.reviewed_by)
    await db.commit(); return {"id": request.id, "status": request.status}


@router.post("/{store_id}/{business_date}/retotal")
async def retotal_store_day(store_id: str, business_date: str, db: AsyncSession = Depends(get_db),
                            user: dict = Depends(require_role("finance", "it"))):
    """Create an immutable new totals version from archived canonical records."""
    scope.require_store(user, store_id)
    await lock_configuration(db)
    day = await get_day(db, store_id, business_date, lock=True)
    if day.status == "Closed":
        raise HTTPException(status_code=409, detail="Reopen the store day with a rationale before re-totaling")
    declared = Decimal("0")
    manifest_currencies: set[str] = set()
    async for manifest in iter_manifests(db, business_date, ("Archived", "Processed")):
        payload = json.loads(manifest.payload)
        if payload.get("store_id") == store_id and str(payload.get("business_date", ""))[:10] == business_date:
            manifest_currencies.add(payload.get("currency", "USD"))
            declared += Decimal(str(payload.get("declared_total", 0)))
    has_manifest = bool(manifest_currencies)
    try:
        definitions = await effective_definitions(
            db, business_date, store_id, validate_all_combined=True
        )
        calculation = await calculate_store_day(db, store_id, business_date, definitions)
        calculated = calculation.source_totals
        currency = single_currency([*calculation.currencies, *manifest_currencies])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    definition_by_key = add_configured_totals(calculated, definitions, calculation.configured_totals)

    await validate_rule_totals(db, calculated, definitions)
    day.status, day.audit_version = "Re-totaling", day.audit_version + 1
    for (name, level, dimension), amount in calculated.items():
        definition = definition_by_key.get((name, level, dimension))
        db.add(AuditTotal(id=str(uuid.uuid4()), store_day_id=day.id, audit_version=day.audit_version,
                          total_name=name, level=level, dimension_key=dimension, calculated_amount=amount,
                          definition_id=definition.id if definition else None,
                          definition_revision=definition.revision if definition else None,
                          currency=currency))
    pos_net = calculated.get(("POS Net", "Store", None), Decimal("0"))
    if has_manifest:
        db.add(AuditTotal(id=str(uuid.uuid4()), store_day_id=day.id, audit_version=day.audit_version,
                          total_name="POS Control Declared", level="Store", calculated_amount=pos_net,
                          declared_amount=declared, variance_amount=pos_net - declared, currency=currency))
    processor_net = calculated.get(("Processor Net", "Store", None), Decimal("0"))
    bank_net = calculated.get(("Bank Net", "Store", None), Decimal("0"))
    gl_net = calculated.get(("ERP Net", "Store", None), Decimal("0"))
    # These comparison totals are retained with every version and are close-blocking when non-zero.
    db.add(AuditTotal(id=str(uuid.uuid4()), store_day_id=day.id, audit_version=day.audit_version,
                      total_name="Processor to Bank Variance", level="Store", calculated_amount=processor_net,
                      declared_amount=bank_net, variance_amount=processor_net - bank_net, currency=currency))
    db.add(AuditTotal(id=str(uuid.uuid4()), store_day_id=day.id, audit_version=day.audit_version,
                      total_name="POS to GL Variance", level="Store", calculated_amount=pos_net,
                      declared_amount=gl_net, variance_amount=pos_net - gl_net, currency=currency))
    await evaluate_configured_rules(db, definitions, calculated, store_id, business_date, currency)
    pending_requests = (await db.execute(select(StoreDayClosureRequest).where(
        StoreDayClosureRequest.store_day_id == day.id, StoreDayClosureRequest.status == "Pending"
    ))).scalars().all()
    for request in pending_requests:
        request.status = "Superseded"
        request.review_reason = f"Totals recalculated to version {day.audit_version}; submit new closure evidence"
        audit(db, "STORE_DAY_CLOSURE_SUPERSEDED", None, "StoreDay", day.id,
              f"Closure request {request.id} superseded by totals version {day.audit_version}",
              actor=user["user"] or "ontology.user")
    day.status, day.totals_stale = "Auditing", False
    audit(db, "STORE_DAY_RETOTALED", None, "StoreDay", day.id,
          f"Store day {store_id}/{business_date} re-totaled (version {day.audit_version})", actor=user["user"] or "ontology.user")
    await db.commit(); await db.refresh(day)
    return {"store_day": store_day_view(day), "transactions": calculation.transaction_count,
            "totals_created": len(calculated) + 2 + int(has_manifest)}


def add_configured_totals(calculated, definitions, configured_totals):
    """Merge configured totals into the version payload and map each to its definition."""
    definition_by_key = {}
    for definition in definitions:
        for dimension, amount in configured_totals.get(definition.id, {}).items():
            key = (f"Configured: {definition.name}", definition.level, dimension)
            calculated[key] = amount
            definition_by_key[key] = definition
    return definition_by_key


async def evaluate_configured_rules(db, definitions, calculated, store_id, business_date, currency):
    """Validate applicable total rules and create cases for newly breached controls."""
    totals_by_name: dict[str, dict[str, Decimal]] = {}
    for (name, level, _dimension), amount in calculated.items():
        level_totals = totals_by_name.setdefault(name, {})
        level_totals[level] = level_totals.get(level, Decimal("0")) + amount

    applicable_totals = set(totals_by_name)
    if not applicable_totals:
        return
    rules = (await db.execute(
        select(ConfiguredAuditRule).where(
            ConfiguredAuditRule.enabled.is_(True),
            ConfiguredAuditRule.total_name.in_(applicable_totals),
        ).order_by(ConfiguredAuditRule.id)
    )).scalars().all()
    definitions_by_total = {f"Configured: {definition.name}": definition for definition in definitions}

    for rule in rules:
        if not rule_applies_to_currency(rule, currency):
            continue
        available_levels = totals_by_name.get(rule.total_name)
        if available_levels is None:
            continue
        observed = available_levels.get("Store")
        if observed is None:
            raise HTTPException(
                status_code=422,
                detail=f"Rule {rule.name} requires a Store-level total",
            )
        if rule.threshold_currency is None:
            definition = definitions_by_total.get(rule.total_name)
            if definition is None or definition.aggregation != "Count":
                continue
        if abs(observed) <= rule.threshold:
            continue
        await create_rule_breach(db, rule, observed, store_id, business_date, currency)


async def validate_rule_totals(db, calculated, definitions) -> None:
    """Reject enabled rules whose calculated total cannot be evaluated at store level."""
    available_levels: dict[str, set[str]] = {}
    for name, level, _dimension in calculated:
        available_levels.setdefault(name, set()).add(level)
    definitions_by_total = {f"Configured: {item.name}": item for item in definitions}
    for total_name, definition in definitions_by_total.items():
        available_levels.setdefault(total_name, set()).add(definition.level)
    names = set(available_levels)
    if not names:
        return
    rules = (await db.execute(select(ConfiguredAuditRule).where(
        ConfiguredAuditRule.enabled.is_(True),
        ConfiguredAuditRule.total_name.in_(names),
    ))).scalars()
    for rule in rules:
        if "Store" not in available_levels[rule.total_name]:
            raise HTTPException(
                status_code=422,
                detail=f"Rule {rule.name} requires a Store-level total",
            )
        if rule.threshold_currency is None:
            definition = definitions_by_total.get(rule.total_name)
            if definition is None or definition.aggregation != "Count":
                raise HTTPException(
                    status_code=422,
                    detail=f"Currency-free rule {rule.name} requires a Count total",
                )


def rule_applies_to_currency(rule, currency: str) -> bool:
    """Currency scoped rules only apply when their currency matches the store day."""
    return rule.threshold_currency is None or rule.threshold_currency == currency


async def create_rule_breach(db, rule, observed: Decimal, store_id: str,
                             business_date: str, currency: str) -> None:
    """Persist one configured-rule exception unless an open breach already exists."""
    case_type = f"Rule breach: {rule.name}"
    existing = (await db.execute(select(Case.id).where(
        Case.store_id == store_id, Case.business_date == business_date,
        Case.case_type == case_type, Case.status.not_in(("Closed",)),
    ))).scalars().first()
    if existing:
        return
    case_id = str(uuid.uuid4())
    amount = abs(observed)
    exposure = amount if rule.threshold_currency is not None else Decimal("0")
    priority = "Critical" if rule.severity == "Critical" else "High" if rule.severity == "High" else "Normal"
    db.add(Case(
        id=case_id, case_number=generate_case_number(business_date), status="Open",
        case_type=case_type, business_date=business_date, store_id=store_id,
        currency=currency if rule.threshold_currency is not None else None,
        total_exception_amount=amount, total_exposure=exposure,
        investigation_status="Pending", evidence_completeness=0, assigned_to=rule.owner,
        priority=priority, sla_due_at=sla_due(priority)))
    await db.flush()
    db.add(DBException(
        id=str(uuid.uuid4()), case_id=case_id, exception_type="CONFIGURED_RULE_VARIANCE",
        exception_family="Configured Control", source_system="Configured Rule",
        detection_origin="Configured Rule", detection_rule_id=rule.id,
        exception_amount=amount, estimated_exposure=exposure, severity=rule.severity,
        confidence="High", close_impact="Yes" if exposure else "No"))
    await db.flush()
    audit(db, "CONFIGURED_RULE_BREACH", case_id, "ConfiguredAuditRule", rule.id,
          f"{rule.name} observed {observed} over threshold {rule.threshold}", actor="total-engine")


@router.post("/{store_id}/{business_date}/close")
async def close_store_day(store_id: str, business_date: str, db: AsyncSession = Depends(get_db),
                          user: dict = Depends(require_role("finance"))):
    scope.require_store(user, store_id)
    await reconciliation.lock_reconciliation(db)
    day = await get_day(db, store_id, business_date, lock=True)
    if day.status == "Closed":
        raise HTTPException(status_code=409, detail="Store day is already closed")
    totals = (await db.execute(select(AuditTotal).where(
        AuditTotal.store_day_id == day.id, AuditTotal.audit_version == day.audit_version,
        AuditTotal.total_name.in_(REQUIRED_COMPARISON_TOTALS | OPTIONAL_COMPARISON_TOTALS), AuditTotal.level == "Store",
    ))).scalars().all()
    blockers = comparison_blockers(day, totals)
    if "totals are stale; re-total the store day" in blockers:
        raise HTTPException(status_code=422, detail="Cannot close: totals are stale; re-total the store day")
    if "current reconciliation totals are missing" in blockers:
        raise HTTPException(status_code=422, detail="Cannot close: current reconciliation totals are missing")
    open_case_count = (await db.execute(select(func.count()).select_from(Case).where(
        Case.store_id == store_id, Case.business_date == business_date,
        Case.status.not_in(("Closed",)),
    ))).scalar_one()
    if open_case_count:
        raise HTTPException(status_code=422, detail=f"Cannot close: {open_case_count} case(s) remain open")
    if "unresolved reconciliation variance" in blockers:
        raise HTTPException(status_code=422, detail="Cannot close: reconciliation totals still have a variance")
    request = (await db.execute(select(StoreDayClosureRequest).where(StoreDayClosureRequest.store_day_id == day.id, StoreDayClosureRequest.status == "Pending").order_by(StoreDayClosureRequest.created_at.desc()))).scalars().first()
    if not request: raise HTTPException(status_code=422, detail="A pending closure request with evidence is required")
    actor = user["user"] or "ontology.user"
    request.status, request.reviewed_by, request.reviewed_at = "Approved", actor, utcnow()
    day.status, day.closed_at, day.closed_by = "Closed", utcnow(), actor
    audit(db, "STORE_DAY_CLOSED", None, "StoreDay", day.id, f"Store day {store_id}/{business_date} closed", actor=actor)
    await db.commit(); await db.refresh(day)
    return store_day_view(day)


@router.post("/{store_id}/{business_date}/reopen")
async def reopen_store_day(store_id: str, business_date: str, body: ReopenRequest, db: AsyncSession = Depends(get_db),
                           user: dict = Depends(require_role("finance"))):
    scope.require_store(user, store_id)
    if not body.rationale.strip():
        raise HTTPException(status_code=422, detail="A reopen rationale is required")
    day = await get_day(db, store_id, business_date, lock=True)
    if day.status != "Closed":
        raise HTTPException(status_code=409, detail="Only a closed store day can be reopened")
    day.status, day.closed_at, day.reopened_reason, day.reopened_by = "Reopened", None, body.rationale.strip(), user["user"] or "ontology.user"
    audit(db, "STORE_DAY_REOPENED", None, "StoreDay", day.id, f"Store day reopened: {body.rationale.strip()}", actor=user["user"] or "ontology.user")
    await db.commit(); await db.refresh(day)
    return store_day_view(day)
