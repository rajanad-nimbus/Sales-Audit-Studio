"""Store-day lifecycle and versioned sales-audit totals."""
import json
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import scope
from auth import require_role
from database import get_db
from models import AuditEvent, AuditTotal, AuditTotalDefinition, CanonicalTransaction, Case, ConfiguredAuditRule, EvidenceRequest, Exception as DBException, FoundationReference, SourceRecord, StoreDay, StoreDayClosureRequest, TransactionAdjustment, utcnow
from nimbus import audit, sla_due

router = APIRouter(prefix="/api/store-days", tags=["Store days"])


class OpenStoreDayBody(BaseModel):
    store_id: str
    business_date: str


async def open_store_day_record(db: AsyncSession, store_id: str, business_date: str, actor: str) -> StoreDay:
    """Create the controlled audit unit for one store and business date. Does not commit.

    There is no scheduler yet, so an IT administrator opens each day. Re-total then calculates against it.
    """
    store_id, business_date = store_id.strip(), business_date.strip()
    try:
        date = datetime.strptime(business_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=422, detail="Business date must be YYYY-MM-DD")
    if date > datetime.now(timezone.utc).date():
        raise HTTPException(status_code=422, detail="A Store Day cannot be opened for a future date")
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
    rationale: str

class ClosureRequestBody(BaseModel):
    evidence_basis: str

class ClosureReviewBody(BaseModel):
    rationale: str


def total_view(total: AuditTotal) -> dict:
    return {"id": total.id, "version": total.audit_version, "name": total.total_name,
            "level": total.level, "dimension": total.dimension_key,
            "calculated_amount": total.calculated_amount, "declared_amount": total.declared_amount,
            "variance_amount": total.variance_amount, "currency": total.currency}


def store_day_view(day: StoreDay) -> dict:
    return {"id": day.id, "store_id": day.store_id, "business_date": day.business_date,
            "status": day.status, "audit_version": day.audit_version, "closed_at": day.closed_at,
            "closed_by": day.closed_by, "reopened_by": day.reopened_by,
            "reopened_reason": day.reopened_reason, "updated_at": day.updated_at}


async def get_day(db: AsyncSession, store_id: str, business_date: str) -> StoreDay:
    day = (await db.execute(select(StoreDay).where(StoreDay.store_id == store_id,
                                                    StoreDay.business_date == business_date))).scalar_one_or_none()
    if not day:
        raise HTTPException(status_code=404, detail="Store day not found; run re-total first")
    return day


@router.get("")
async def list_store_days(store_id: str | None = None, limit: int = 100, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance", "it"))):
    query = scope.restrict(select(StoreDay), user, StoreDay.store_id).order_by(StoreDay.business_date.desc(), StoreDay.store_id).limit(min(limit, 500))
    if store_id:
        query = query.where(StoreDay.store_id == store_id)
    rows = (await db.execute(query)).scalars().all()
    return [store_day_view(row) for row in rows]


@router.get("/stores")
async def store_selector(db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance", "it"))):
    if scope.scoped(user):
        return sorted(scope.allowed_stores(user) or [])
    transaction_stores = (await db.execute(select(CanonicalTransaction.store_id).distinct())).scalars().all()
    store_day_stores = (await db.execute(select(StoreDay.store_id).distinct())).scalars().all()
    return sorted(set(transaction_stores) | set(store_day_stores))


@router.get("/{store_id}/{business_date}")
async def get_store_day(store_id: str, business_date: str, version: int | None = None, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance", "it"))):
    scope.require_store(user, store_id)
    day = await get_day(db, store_id, business_date)
    selected_version = version if version is not None else day.audit_version
    totals = (await db.execute(select(AuditTotal).where(AuditTotal.store_day_id == day.id,
                                                        AuditTotal.audit_version == selected_version)
                               .order_by(AuditTotal.level, AuditTotal.total_name, AuditTotal.dimension_key))).scalars().all()
    versions = (await db.execute(select(AuditTotal.audit_version).where(AuditTotal.store_day_id == day.id).distinct().order_by(AuditTotal.audit_version.desc()))).scalars().all()
    cases = (await db.execute(select(Case).where(Case.store_id == store_id, Case.business_date == business_date)
                              .order_by(Case.created_at.desc()))).scalars().all()
    blockers = []
    open_cases = [case for case in cases if case.status != "Closed"]
    if open_cases: blockers.append(f"{len(open_cases)} open case(s)")
    if selected_version == day.audit_version:
        variances = [total for total in totals if total.total_name in ("Processor to Bank Variance", "POS to GL Variance") and total.variance_amount]
        if variances: blockers.append("unresolved reconciliation variance")
    requests = (await db.execute(select(StoreDayClosureRequest).where(StoreDayClosureRequest.store_day_id == day.id).order_by(StoreDayClosureRequest.created_at.desc()))).scalars().all()
    history = (await db.execute(select(AuditEvent).where(AuditEvent.object_type == "StoreDay", AuditEvent.object_id == day.id).order_by(AuditEvent.seq.desc()).limit(100))).scalars().all()
    # Cases closed over an evidence gap leave the open-case blocker, so list them here to keep them visible.
    overrides = (await db.execute(
        select(EvidenceRequest, Case).join(Case, Case.id == EvidenceRequest.case_id)
        .where(Case.store_id == store_id, Case.business_date == business_date,
               EvidenceRequest.status.in_(["Overridden", "Override Pending"]))
        .order_by(EvidenceRequest.created_at.desc()))).all()
    evidence_overrides = [{"case_id": c.id, "case_number": c.case_number, "requirement": r.requirement, "status": r.status,
                           "reason": r.override_reason, "financial_impact": str(r.override_financial_impact),
                           "requested_by": r.override_requested_by, "approved_by": r.override_approved_by} for r, c in overrides]
    return {"store_day": store_day_view(day), "selected_version": selected_version, "versions": versions,
            "close_blockers": blockers, "evidence_overrides": evidence_overrides, "totals": [total_view(total) for total in totals],
            "cases": [{"id": case.id, "case_number": case.case_number, "case_type": case.case_type,
                       "status": case.status, "exposure": case.total_exposure} for case in cases],
            "closure_requests": [{"id": item.id, "requested_by": item.requested_by, "evidence_basis": item.evidence_basis, "status": item.status, "reviewed_by": item.reviewed_by, "review_reason": item.review_reason, "created_at": item.created_at} for item in requests],
            "history": [{"id": event.id, "event_type": event.event_type, "actor": event.actor, "description": event.action_description, "created_at": event.created_at} for event in history]}

@router.post("/{store_id}/{business_date}/closure-requests")
async def request_store_day_closure(store_id: str, business_date: str, body: ClosureRequestBody, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance", "it"))):
    scope.require_store(user, store_id)
    if not body.evidence_basis.strip(): raise HTTPException(status_code=422, detail="Closure evidence basis is required")
    day = await get_day(db, store_id, business_date)
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
    day = await get_day(db, store_id, business_date)
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
    day = (await db.execute(select(StoreDay).where(StoreDay.store_id == store_id,
                                                    StoreDay.business_date == business_date))).scalar_one_or_none()
    if day and day.status == "Closed":
        raise HTTPException(status_code=409, detail="Reopen the store day with a rationale before re-totaling")
    if not day:
        raise HTTPException(status_code=404, detail="No Store Day for that store and date. An IT administrator must open it first.")
    day.status, day.audit_version = "Re-totaling", day.audit_version + 1
    rows = (await db.execute(select(CanonicalTransaction).where(CanonicalTransaction.store_id == store_id,
                                                                  CanonicalTransaction.business_date == business_date))).scalars().all()
    calculated: dict[tuple[str, str, str | None], Decimal] = defaultdict(Decimal)
    for row in rows:
        source = {"pos-map-v1": "POS", "processor-map-v1": "Processor", "bank-map-v1": "Bank", "erp-map-v1": "ERP"}.get(row.source_lineage)
        if source:
            calculated[(f"{source} Net", "Store", None)] += row.signed_amount
        if source == "POS":
            calculated[("POS Net", "Tender", row.tender_type or "Unspecified")] += row.signed_amount
            calculated[("POS Net", "Register", row.register_id or "Unspecified")] += row.signed_amount

    # Corrections remain separate from source records. Their approved monetary effect is visible
    # as its own total and is applied only to the computed Store Day view.
    adjustments = (await db.execute(select(TransactionAdjustment).where(
        TransactionAdjustment.transaction_id.in_([row.id for row in rows]),
        TransactionAdjustment.status == "Approved"))).scalars().all() if rows else []
    row_by_id = {row.id: row for row in rows}
    adjustment_net = Decimal("0")
    for adjustment in adjustments:
        values, transaction = adjustment.proposed_values, row_by_id[adjustment.transaction_id]
        if "amount_delta" in values:
            adjustment_net += Decimal(str(values["amount_delta"]))
        elif "corrected_amount" in values:
            adjustment_net += Decimal(str(values["corrected_amount"])) - transaction.signed_amount
    if adjustment_net:
        calculated[("Approved Adjustment Net", "Store", None)] = adjustment_net
        calculated[("POS Net", "Store", None)] += adjustment_net

    definitions = (await db.execute(select(AuditTotalDefinition).where(AuditTotalDefinition.active == True))).scalars().all()  # noqa: E712
    for definition in definitions:
        candidates = rows
        if definition.source_system: candidates = [row for row in candidates if row.source_lineage.startswith(definition.source_system.lower())]
        if definition.transaction_types: candidates = [row for row in candidates if row.transaction_type in definition.transaction_types]
        if definition.tender_type: candidates = [row for row in candidates if row.tender_type == definition.tender_type]
        groups = defaultdict(list)
        for row in candidates:
            key = None if definition.level == "Store" else row.register_id or "Unspecified" if definition.level == "Register" else row.tender_type or "Unspecified"
            groups[key].append(row)
        for dimension, grouped in groups.items():
            value = sum((row.signed_amount for row in grouped), Decimal("0")) if definition.aggregation == "Sum" else Decimal(len(grouped))
            calculated[(f"Configured: {definition.name}", definition.level, dimension)] = value

    # POS control manifests are declared totals, preserved as source evidence rather than overwritten.
    manifests = (await db.execute(select(SourceRecord).where(SourceRecord.source_system == "POSControl",
                                                              SourceRecord.status.in_(("Archived", "Processed"))))).scalars().all()
    declared = Decimal("0")
    for manifest in manifests:
        payload = json.loads(manifest.payload)
        if payload.get("store_id") == store_id and str(payload.get("business_date", ""))[:10] == business_date:
            declared += Decimal(str(payload.get("declared_total", 0)))
    for (name, level, dimension), amount in calculated.items():
        db.add(AuditTotal(id=str(uuid.uuid4()), store_day_id=day.id, audit_version=day.audit_version,
                          total_name=name, level=level, dimension_key=dimension, calculated_amount=amount,
                          currency="USD"))
    rules = (await db.execute(select(ConfiguredAuditRule).where(ConfiguredAuditRule.enabled == True))).scalars().all()  # noqa: E712
    for rule in rules:
        observed = sum((amount for (name, _level, _dimension), amount in calculated.items() if name == rule.total_name), Decimal("0"))
        if abs(observed) <= rule.threshold:
            continue
        case_type = f"Rule breach: {rule.name}"
        existing = (await db.execute(select(Case.id).where(Case.store_id == store_id, Case.business_date == business_date,
                                                           Case.case_type == case_type, Case.status.not_in(("Closed",))))).scalars().first()
        if existing:
            continue
        case_id = str(uuid.uuid4())
        priority = "Critical" if rule.severity == "Critical" else "High" if rule.severity == "High" else "Normal"
        db.add(Case(id=case_id, case_number=f"ZA-{business_date.replace('-', '')}-{uuid.uuid4().hex[:4].upper()}", status="Open", case_type=case_type, business_date=business_date, store_id=store_id, total_exception_amount=abs(observed), total_exposure=abs(observed), investigation_status="Pending", evidence_completeness=0, assigned_to=rule.owner, priority=priority, sla_due_at=sla_due(priority)))
        db.add(DBException(id=str(uuid.uuid4()), case_id=case_id, exception_type="CONFIGURED_RULE_VARIANCE", exception_family="Configured Control", source_system="Configured Rule", detection_origin="Configured Rule", detection_rule_id=rule.id, exception_amount=abs(observed), estimated_exposure=abs(observed), severity=rule.severity, confidence="High", close_impact="Yes"))
        await db.flush()
        audit(db, "CONFIGURED_RULE_BREACH", case_id, "ConfiguredAuditRule", rule.id, f"{rule.name} observed {observed} over threshold {rule.threshold}", actor="total-engine")
    pos_net = calculated.get(("POS Net", "Store", None), Decimal("0"))
    if declared or any(m.source_system == "POSControl" for m in manifests):
        db.add(AuditTotal(id=str(uuid.uuid4()), store_day_id=day.id, audit_version=day.audit_version,
                          total_name="POS Control Declared", level="Store", calculated_amount=pos_net,
                          declared_amount=declared, variance_amount=pos_net - declared, currency="USD"))
    processor_net = calculated.get(("Processor Net", "Store", None), Decimal("0"))
    bank_net = calculated.get(("Bank Net", "Store", None), Decimal("0"))
    gl_net = calculated.get(("ERP Net", "Store", None), Decimal("0"))
    # These comparison totals are retained with every version and are close-blocking when non-zero.
    db.add(AuditTotal(id=str(uuid.uuid4()), store_day_id=day.id, audit_version=day.audit_version,
                      total_name="Processor to Bank Variance", level="Store", calculated_amount=processor_net,
                      declared_amount=bank_net, variance_amount=processor_net - bank_net, currency="USD"))
    db.add(AuditTotal(id=str(uuid.uuid4()), store_day_id=day.id, audit_version=day.audit_version,
                      total_name="POS to GL Variance", level="Store", calculated_amount=pos_net,
                      declared_amount=gl_net, variance_amount=pos_net - gl_net, currency="USD"))
    day.status = "Auditing"
    audit(db, "STORE_DAY_RETOTALED", None, "StoreDay", day.id,
          f"Store day {store_id}/{business_date} re-totaled (version {day.audit_version})", actor=user["user"] or "ontology.user")
    await db.commit(); await db.refresh(day)
    return {"store_day": store_day_view(day), "transactions": len(rows), "totals_created": len(calculated) + 2 + (1 if declared else 0)}


@router.post("/{store_id}/{business_date}/close")
async def close_store_day(store_id: str, business_date: str, db: AsyncSession = Depends(get_db),
                          user: dict = Depends(require_role("finance"))):
    scope.require_store(user, store_id)
    day = await get_day(db, store_id, business_date)
    open_cases = (await db.execute(select(Case.id).where(Case.store_id == store_id, Case.business_date == business_date,
                                                           Case.status.not_in(("Closed",))))).scalars().all()
    if open_cases:
        raise HTTPException(status_code=422, detail=f"Cannot close: {len(open_cases)} case(s) remain open")
    variances = (await db.execute(select(AuditTotal).where(AuditTotal.store_day_id == day.id,
                                                           AuditTotal.audit_version == day.audit_version,
                                                           AuditTotal.total_name.in_(("Processor to Bank Variance", "POS to GL Variance")),
                                                           AuditTotal.variance_amount != 0))).scalars().all()
    if variances:
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
    day = await get_day(db, store_id, business_date)
    if day.status != "Closed":
        raise HTTPException(status_code=409, detail="Only a closed store day can be reopened")
    day.status, day.closed_at, day.reopened_reason, day.reopened_by = "Reopened", None, body.rationale.strip(), user["user"] or "ontology.user"
    audit(db, "STORE_DAY_REOPENED", None, "StoreDay", day.id, f"Store day reopened: {body.rationale.strip()}", actor=user["user"] or "ontology.user")
    await db.commit(); await db.refresh(day)
    return store_day_view(day)
