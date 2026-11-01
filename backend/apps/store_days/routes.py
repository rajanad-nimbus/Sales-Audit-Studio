"""Store-day lifecycle and versioned sales-audit totals."""

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from database import get_db
from fastapi import APIRouter, Depends, HTTPException, Query
from integrations.ontology.nimbus import audit
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select, union
from sqlalchemy.ext.asyncio import AsyncSession

from apps.audit.models import AuditEvent
from apps.cases.models import Case, EvidenceRequest
from apps.controls.foundation.models import FoundationReference
from apps.ingest import reconciliation
from apps.ingest.manifests import iter_manifests, validate_business_date
from apps.security import scope
from apps.security.authentication import require_role
from apps.store_days.closures import (
    ClosureRequestBody,
    ClosureReviewBody,
    ReopenRequest,
    close_store_day,
    closure_router,
    reject_store_day_closure,
    reopen_store_day,
    request_store_day_closure,
)
from apps.store_days.freshness import lock_configuration, lock_store_day_identity
from apps.store_days.models import AuditTotal, StoreDay, StoreDayClosureRequest
from apps.store_days.rule_evaluation import (
    add_configured_totals,
    evaluate_configured_rules,
    prepare_rule_evaluation,
)
from apps.store_days.service import (
    comparison_blockers,
    get_day,
    store_day_view,
    total_view,
)
from apps.totals.calculation import calculate_store_day, effective_definitions
from apps.totals.currency import single_currency
from apps.transactions.models import CanonicalTransaction

router = APIRouter(prefix="/api/store-days", tags=["Store days"])
MAX_STORE_DAY_HISTORY_ITEMS = 200
__all__ = [
    "ClosureRequestBody",
    "ClosureReviewBody",
    "ReopenRequest",
    "close_store_day",
    "reject_store_day_closure",
    "reopen_store_day",
    "request_store_day_closure",
    "reconciliation",
]


class OpenStoreDayBody(BaseModel):
    store_id: str = Field(min_length=1, max_length=50)
    business_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


async def open_store_day_record(
    db: AsyncSession, store_id: str, business_date: str, actor: str
) -> StoreDay:
    """Create the controlled audit unit for one store and business date. Does not commit.

    There is no scheduler yet, so an IT administrator opens each day. Re-total then calculates against it.
    """
    await lock_configuration(db)
    store_id = store_id.strip()
    if not store_id or len(store_id) > 50:
        raise HTTPException(
            status_code=422, detail="store_id must contain 1 to 50 non-whitespace characters"
        )
    try:
        validate_business_date(business_date)
        date = datetime.fromisoformat(business_date).date()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if date > datetime.now(UTC).date():
        raise HTTPException(
            status_code=422, detail="A Store Day cannot be opened for a future date"
        )
    await lock_store_day_identity(db, store_id, business_date)
    known = (
        await db.execute(
            select(FoundationReference.id)
            .where(
                FoundationReference.category == "Store",
                FoundationReference.code == store_id,
                FoundationReference.active.is_(True),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if not known:
        known = (
            await db.execute(
                select(CanonicalTransaction.id)
                .where(CanonicalTransaction.store_id == store_id)
                .limit(1)
            )
        ).scalar_one_or_none()
    if not known:
        raise HTTPException(
            status_code=422, detail=f"Unknown store {store_id}. Add it to the store master first."
        )
    if (
        await db.execute(
            select(StoreDay.id).where(
                StoreDay.store_id == store_id, StoreDay.business_date == business_date
            )
        )
    ).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="That Store Day is already open")
    day = StoreDay(
        id=str(uuid.uuid4()), store_id=store_id, business_date=business_date, status="Open"
    )
    db.add(day)
    audit(
        db,
        "STORE_DAY_OPENED",
        None,
        "StoreDay",
        day.id,
        f"Store day {store_id}/{business_date} opened",
        actor=actor,
    )
    await db.flush()
    return day


@router.post("")
async def open_store_day(
    body: OpenStoreDayBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("it")),
):
    day = await open_store_day_record(
        db, body.store_id, body.business_date, user["user"] or "ontology.user"
    )
    await db.commit()
    await db.refresh(day)
    return store_day_view(day)


@router.get("")
async def list_store_days(
    store_id: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it")),
):
    query = (
        scope.restrict(select(StoreDay), user, StoreDay.store_id)
        .order_by(StoreDay.business_date.desc(), StoreDay.store_id)
        .limit(min(limit, 500))
    )
    if store_id:
        query = query.where(StoreDay.store_id == store_id)
    rows = (await db.execute(query)).scalars().all()
    return [store_day_view(row) for row in rows]


@router.get("/stores")
async def store_selector(
    db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance", "it"))
):
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
async def get_store_day(
    store_id: str,
    business_date: str,
    version: Annotated[int | None, Query(ge=1)] = None,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it")),
):
    scope.require_store(user, store_id)
    day = await get_day(db, store_id, business_date)
    selected_version = version if version is not None else day.audit_version
    totals = (
        (
            await db.execute(
                select(AuditTotal)
                .where(
                    AuditTotal.store_day_id == day.id, AuditTotal.audit_version == selected_version
                )
                .order_by(AuditTotal.level, AuditTotal.total_name, AuditTotal.dimension_key)
            )
        )
        .scalars()
        .all()
    )
    versions = (
        (
            await db.execute(
                select(AuditTotal.audit_version)
                .where(AuditTotal.store_day_id == day.id)
                .distinct()
                .order_by(AuditTotal.audit_version.desc())
                .limit(MAX_STORE_DAY_HISTORY_ITEMS + 1)
            )
        )
        .scalars()
        .all()
    )
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
    cases = (
        (
            await db.execute(
                case_query.order_by(Case.created_at.desc(), Case.id.desc()).limit(
                    MAX_STORE_DAY_HISTORY_ITEMS + 1
                )
            )
        )
        .scalars()
        .all()
    )
    cases_truncated = len(cases) > MAX_STORE_DAY_HISTORY_ITEMS
    cases = cases[:MAX_STORE_DAY_HISTORY_ITEMS]
    blockers = []
    if open_case_count:
        blockers.append(f"{open_case_count} open case(s)")
    current_totals = (
        totals
        if selected_version == day.audit_version
        else (
            await db.execute(
                select(AuditTotal).where(
                    AuditTotal.store_day_id == day.id, AuditTotal.audit_version == day.audit_version
                )
            )
        )
        .scalars()
        .all()
    )
    blockers.extend(comparison_blockers(day, current_totals))
    requests = (
        (
            await db.execute(
                select(StoreDayClosureRequest)
                .where(StoreDayClosureRequest.store_day_id == day.id)
                .order_by(
                    StoreDayClosureRequest.created_at.desc(), StoreDayClosureRequest.id.desc()
                )
                .limit(MAX_STORE_DAY_HISTORY_ITEMS + 1)
            )
        )
        .scalars()
        .all()
    )
    requests_truncated = len(requests) > MAX_STORE_DAY_HISTORY_ITEMS
    requests = requests[:MAX_STORE_DAY_HISTORY_ITEMS]
    history = (
        (
            await db.execute(
                select(AuditEvent)
                .where(AuditEvent.object_type == "StoreDay", AuditEvent.object_id == day.id)
                .order_by(AuditEvent.seq.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    # Cases closed over an evidence gap leave the open-case blocker, so list them here to keep them visible.
    overrides = (
        await db.execute(
            select(EvidenceRequest, Case)
            .join(Case, Case.id == EvidenceRequest.case_id)
            .where(
                Case.store_id == store_id,
                Case.business_date == business_date,
                EvidenceRequest.status.in_(["Overridden", "Override Pending"]),
            )
            .order_by(EvidenceRequest.created_at.desc(), EvidenceRequest.id.desc())
            .limit(MAX_STORE_DAY_HISTORY_ITEMS + 1)
        )
    ).all()
    overrides_truncated = len(overrides) > MAX_STORE_DAY_HISTORY_ITEMS
    overrides = overrides[:MAX_STORE_DAY_HISTORY_ITEMS]
    evidence_overrides = [
        {
            "case_id": c.id,
            "case_number": c.case_number,
            "requirement": r.requirement,
            "status": r.status,
            "reason": r.override_reason,
            "financial_impact": str(r.override_financial_impact),
            "requested_by": r.override_requested_by,
            "approved_by": r.override_approved_by,
        }
        for r, c in overrides
    ]
    return {
        "store_day": store_day_view(day),
        "selected_version": selected_version,
        "versions": versions,
        "versions_truncated": versions_truncated,
        "case_count": case_count,
        "cases_truncated": cases_truncated,
        "closure_requests_truncated": requests_truncated,
        "evidence_overrides_truncated": overrides_truncated,
        "close_blockers": blockers,
        "evidence_overrides": evidence_overrides,
        "totals": [total_view(total) for total in totals],
        "cases": [
            {
                "id": case.id,
                "case_number": case.case_number,
                "case_type": case.case_type,
                "status": case.status,
                "exposure": case.total_exposure,
            }
            for case in cases
        ],
        "closure_requests": [
            {
                "id": item.id,
                "requested_by": item.requested_by,
                "evidence_basis": item.evidence_basis,
                "status": item.status,
                "reviewed_by": item.reviewed_by,
                "review_reason": item.review_reason,
                "created_at": item.created_at,
            }
            for item in requests
        ],
        "history": [
            {
                "id": event.id,
                "event_type": event.event_type,
                "actor": event.actor,
                "description": event.action_description,
                "created_at": event.created_at,
            }
            for event in history
        ],
    }


@router.post("/{store_id}/{business_date}/retotal")
async def retotal_store_day(
    store_id: str,
    business_date: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it")),
):
    """Create an immutable new totals version from archived canonical records."""
    scope.require_store(user, store_id)
    await lock_configuration(db)
    day = await get_day(db, store_id, business_date, lock=True)
    if day.status == "Closed":
        raise HTTPException(
            status_code=409, detail="Reopen the store day with a rationale before re-totaling"
        )
    declared = Decimal("0")
    manifest_currencies: set[str] = set()
    async for manifest in iter_manifests(db, business_date, ("Archived", "Processed")):
        payload = json.loads(manifest.payload)
        if (
            payload.get("store_id") == store_id
            and str(payload.get("business_date", ""))[:10] == business_date
        ):
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
    definition_by_key = add_configured_totals(
        calculated, definitions, calculation.configured_totals
    )

    rule_evaluation = await prepare_rule_evaluation(db, calculated, definitions, currency)
    day.status, day.audit_version = "Re-totaling", day.audit_version + 1
    for (name, level, dimension), amount in calculated.items():
        definition = definition_by_key.get((name, level, dimension))
        db.add(
            AuditTotal(
                id=str(uuid.uuid4()),
                store_day_id=day.id,
                audit_version=day.audit_version,
                total_name=name,
                level=level,
                dimension_key=dimension,
                calculated_amount=amount,
                definition_id=definition.id if definition else None,
                definition_revision=definition.revision if definition else None,
                currency=currency,
            )
        )
    pos_net = calculated.get(("POS Net", "Store", None), Decimal("0"))
    if has_manifest:
        db.add(
            AuditTotal(
                id=str(uuid.uuid4()),
                store_day_id=day.id,
                audit_version=day.audit_version,
                total_name="POS Control Declared",
                level="Store",
                calculated_amount=pos_net,
                declared_amount=declared,
                variance_amount=pos_net - declared,
                currency=currency,
            )
        )
    processor_net = calculated.get(("Processor Net", "Store", None), Decimal("0"))
    bank_net = calculated.get(("Bank Net", "Store", None), Decimal("0"))
    gl_net = calculated.get(("ERP Net", "Store", None), Decimal("0"))
    # These comparison totals are retained with every version and are close-blocking when non-zero.
    db.add(
        AuditTotal(
            id=str(uuid.uuid4()),
            store_day_id=day.id,
            audit_version=day.audit_version,
            total_name="Processor to Bank Variance",
            level="Store",
            calculated_amount=processor_net,
            declared_amount=bank_net,
            variance_amount=processor_net - bank_net,
            currency=currency,
        )
    )
    db.add(
        AuditTotal(
            id=str(uuid.uuid4()),
            store_day_id=day.id,
            audit_version=day.audit_version,
            total_name="POS to GL Variance",
            level="Store",
            calculated_amount=pos_net,
            declared_amount=gl_net,
            variance_amount=pos_net - gl_net,
            currency=currency,
        )
    )
    await evaluate_configured_rules(db, rule_evaluation, store_id, business_date, currency)
    pending_requests = (
        (
            await db.execute(
                select(StoreDayClosureRequest).where(
                    StoreDayClosureRequest.store_day_id == day.id,
                    StoreDayClosureRequest.status == "Pending",
                )
            )
        )
        .scalars()
        .all()
    )
    for request in pending_requests:
        request.status = "Superseded"
        request.review_reason = (
            f"Totals recalculated to version {day.audit_version}; submit new closure evidence"
        )
        audit(
            db,
            "STORE_DAY_CLOSURE_SUPERSEDED",
            None,
            "StoreDay",
            day.id,
            f"Closure request {request.id} superseded by totals version {day.audit_version}",
            actor=user["user"] or "ontology.user",
        )
    day.status, day.totals_stale = "Auditing", False
    audit(
        db,
        "STORE_DAY_RETOTALED",
        None,
        "StoreDay",
        day.id,
        f"Store day {store_id}/{business_date} re-totaled (version {day.audit_version})",
        actor=user["user"] or "ontology.user",
    )
    await db.commit()
    await db.refresh(day)
    return {
        "store_day": store_day_view(day),
        "transactions": calculation.transaction_count,
        "totals_created": len(calculated) + 2 + int(has_manifest),
    }


router.include_router(closure_router)
