"""Case lifecycle, list, summary, and detail endpoints."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal

from database import get_db
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from integrations.ontology import nimbus
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.audit.models import AuditEvent
from apps.cases.identifiers import generate_case_number
from apps.cases.models import Case, Finding, HumanDecision, Recommendation
from apps.cases.models import Exception as DBException
from apps.cases.schemas import Case as CaseSchema
from apps.cases.schemas import CaseAmountReview, CaseCreate, CaseDetailResponse, CaseUpdate
from apps.corrections.follow_up import create_follow_up
from apps.security import scope
from apps.security.authentication import require_role
from apps.transactions.detail import MAX_MONEY

router = APIRouter()
MAX_CASE_DETAIL_ITEMS = 200


async def case_detail_records(query, db: AsyncSession) -> tuple[list, bool, str | None]:
    rows = (await db.execute(query.limit(MAX_CASE_DETAIL_ITEMS + 1))).scalars().all()
    truncated = len(rows) > MAX_CASE_DETAIL_ITEMS
    rows = rows[:MAX_CASE_DETAIL_ITEMS]
    return rows, truncated, rows[-1].id if truncated else None


@router.get("/api/cases", response_model=list[CaseSchema])
async def list_cases(
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("analyst", "finance", "it", "auditor")),
):
    query = scope.restrict(select(Case), user, Case.store_id)
    query = query.order_by(Case.created_at.desc(), Case.id).offset(skip).limit(limit)
    return (await db.execute(query)).scalars().all()


@router.get("/api/cases/status/{status}", response_model=list[CaseSchema])
async def list_cases_by_status(
    status: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("analyst", "finance", "it", "auditor")),
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    response: Response = None,
):
    base = scope.restrict(select(Case).where(Case.status == status), user, Case.store_id)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    query = base.order_by(Case.updated_at.desc(), Case.id).offset(skip).limit(limit)
    rows = (await db.execute(query)).scalars().all()
    if response is not None:
        response.headers["X-Total-Count"] = str(total)
        response.headers["X-Has-More"] = str(skip + len(rows) < total).lower()
    return rows


@router.get("/api/cases/count")
async def count_cases(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("analyst", "finance", "it", "auditor")),
):
    query = scope.restrict(select(func.count(Case.id)), user, Case.store_id)
    return {"count": (await db.execute(query)).scalar()}


@router.get("/api/cases/stats")
async def case_stats(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("analyst", "finance", "it", "auditor")),
):
    statuses = ("Open", "In Investigation", "In Review", "Closed")
    result = (await db.execute(
        scope.restrict(
            select(Case.status, func.count(Case.id)).where(Case.status.in_(statuses)).group_by(Case.status),
            user,
            Case.store_id,
        )
    )).all()
    counts = {status: count for status, count in result}
    by_status = {status: counts.get(status, 0) for status in statuses}
    return {"by_status": by_status, "total": sum(by_status.values())}


@router.get("/api/cases/{case_id}", response_model=CaseDetailResponse)
async def get_case_detail(
    case_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("analyst", "finance", "it", "auditor")),
):
    case = (await db.execute(
        scope.restrict(select(Case).where(Case.id == case_id), user, Case.store_id)
    )).scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")

    exceptions, exceptions_truncated, exceptions_cursor = await case_detail_records(
        select(DBException).where(DBException.case_id == case_id)
        .order_by(DBException.created_at.desc(), DBException.id.desc()), db,
    )
    findings, findings_truncated, findings_cursor = await case_detail_records(
        select(Finding).where(Finding.case_id == case_id)
        .order_by(Finding.created_at.desc(), Finding.id.desc()), db,
    )
    recommendations, recommendations_truncated, recommendations_cursor = await case_detail_records(
        select(Recommendation).where(Recommendation.case_id == case_id)
        .order_by(Recommendation.created_at.desc(), Recommendation.id.desc()), db,
    )
    decisions, decisions_truncated, decisions_cursor = await case_detail_records(
        select(HumanDecision).where(HumanDecision.case_id == case_id)
        .order_by(HumanDecision.created_at.desc(), HumanDecision.id.desc()), db,
    )
    return CaseDetailResponse(
        case=case,
        exceptions=exceptions,
        findings=findings,
        recommendations=recommendations,
        decisions=decisions,
        truncated={
            "exceptions": exceptions_truncated,
            "findings": findings_truncated,
            "recommendations": recommendations_truncated,
            "decisions": decisions_truncated,
        },
        next_cursor={
            "exceptions": exceptions_cursor,
            "findings": findings_cursor,
            "recommendations": recommendations_cursor,
            "decisions": decisions_cursor,
        },
    )


@router.get("/api/cases/{case_id}/history/{section}")
async def get_case_history(
    case_id: str,
    section: Literal["exceptions", "findings", "recommendations", "decisions"],
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("analyst", "finance", "it", "auditor")),
    cursor_id: Annotated[str | None, Query(min_length=1, max_length=36)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_CASE_DETAIL_ITEMS)] = 100,
):
    """Page through a case's bounded child history without loading other sections."""
    case = (await db.execute(
        scope.restrict(select(Case.id).where(Case.id == case_id), user, Case.store_id)
    )).scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")

    ordering = {
        "exceptions": (DBException.created_at.desc(), DBException.id.desc()),
        "findings": (Finding.created_at.desc(), Finding.id.desc()),
        "recommendations": (Recommendation.created_at.desc(), Recommendation.id.desc()),
        "decisions": (HumanDecision.created_at.desc(), HumanDecision.id.desc()),
    }[section]
    model = {
        "exceptions": DBException,
        "findings": Finding,
        "recommendations": Recommendation,
        "decisions": HumanDecision,
    }[section]
    base = select(model).where(model.case_id == case_id)
    total = (await db.execute(
        select(func.count()).select_from(base.with_only_columns(model.id).subquery())
    )).scalar_one()
    if cursor_id is not None:
        cursor = (await db.execute(select(model).where(model.case_id == case_id, model.id == cursor_id))).scalar_one_or_none()
        if cursor is None:
            raise HTTPException(status_code=422, detail="History cursor does not belong to this case and section")
        created_at = cursor.created_at
        created_at_column = model.created_at
        base = base.where(or_(
            created_at_column < created_at,
            (created_at_column == created_at) & (model.id < cursor.id),
        ))
    rows = (await db.execute(base.order_by(*ordering).limit(limit + 1))).scalars().all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "items": rows,
        "total": total,
        "limit": limit,
        "has_more": has_more,
        "next_cursor": rows[-1].id if has_more else None,
    }


@router.post("/api/cases", response_model=CaseSchema)
async def create_case(
    case: CaseCreate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it")),
):
    if case.priority not in nimbus.SLA_HOURS:
        raise HTTPException(status_code=422, detail="Invalid priority")
    case_id = str(uuid.uuid4())
    case_number = generate_case_number(datetime.now(timezone.utc).strftime("%Y%m%d"))
    db_case = Case(
        id=case_id,
        case_number=case_number,
        sla_due_at=nimbus.sla_due(case.priority),
        **case.model_dump(),
    )
    db.add(db_case)
    await db.flush()
    db.add(AuditEvent(
        id=str(uuid.uuid4()),
        event_type="CASE_CREATED",
        actor=user.get("user") or "ontology.user",
        object_type="Case",
        object_id=case_id,
        case_id=case_id,
        action_description=f"Case created: {case.case_type}",
        after_state=case.model_dump(),
    ))
    await db.commit()
    await db.refresh(db_case)
    return db_case


@router.put("/api/cases/{case_id}", response_model=CaseSchema)
async def update_case(
    case_id: str,
    case_update: CaseUpdate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it")),
):
    db_case = (await db.execute(
        select(Case).where(Case.id == case_id).with_for_update()
        .execution_options(populate_existing=True)
    )).scalar_one_or_none()
    if db_case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    scope.require_store(user, db_case.store_id)
    old_state = {
        "status": db_case.status,
        "assigned_to": db_case.assigned_to,
        "priority": db_case.priority,
        "currency": db_case.currency,
    }
    if "status" in case_update.model_fields_set:
        raise HTTPException(status_code=422, detail="Case status is controlled by workflow transitions")
    if "priority" in case_update.model_fields_set and case_update.priority not in nimbus.SLA_HOURS:
        raise HTTPException(status_code=422, detail="Invalid priority")
    if case_update.priority is not None and user["role"] not in ("finance", "admin"):
        raise HTTPException(status_code=403, detail="Changing priority requires finance role")
    if "currency" in case_update.model_fields_set:
        from apps.totals.currency import validate_currency

        if user["role"] not in ("finance", "admin"):
            raise HTTPException(status_code=403, detail="Setting currency requires finance role")
        if db_case.currency is not None:
            raise HTTPException(status_code=409, detail="Case currency is already set")
        try:
            validate_currency(case_update.currency)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    changes = case_update.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(db_case, key, value)
    db.add(AuditEvent(
        id=str(uuid.uuid4()),
        event_type="CASE_UPDATED",
        actor=user.get("user") or "ontology.user",
        object_type="Case",
        object_id=case_id,
        case_id=case_id,
        action_description="Case updated",
        before_state=old_state,
        after_state=changes,
    ))
    await db.commit()
    await db.refresh(db_case)
    return db_case


@router.post("/api/cases/{case_id}/amount-review", response_model=CaseSchema)
async def review_case_amounts(
    case_id: str,
    body: CaseAmountReview,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance")),
):
    case = (await db.execute(
        select(Case).where(Case.id == case_id).with_for_update()
        .execution_options(populate_existing=True)
    )).scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    scope.require_store(user, case.store_id)
    if case.status == "Closed":
        raise HTTPException(status_code=409, detail="Closed case amounts cannot be changed; a follow-up review is required")
    if case.status in {"Resolving", "Pending Validation"}:
        raise HTTPException(status_code=409, detail="Case has an active workflow")
    if not case.amount_needs_review:
        raise HTTPException(status_code=409, detail="Case amounts do not require review")
    if not body.rationale.strip():
        raise HTTPException(status_code=422, detail="Review rationale is required")
    exceptions = (await db.execute(
        select(DBException).where(DBException.case_id == case_id).with_for_update()
    )).scalars().all()
    totals = validate_case_amount_review(body, exceptions)
    before = case_amount_snapshot(case, exceptions)
    apply_case_amount_review(case, exceptions, body, totals)
    case.amount_needs_review = False
    nimbus.audit(
        db,
        "CASE_AMOUNTS_REVIEWED",
        case.id,
        "Case",
        case.id,
        f"Finance reviewed case amounts: {body.rationale.strip()}",
        actor=user["user"] or "ontology.user",
        after={
            "before": before,
            "amount": str(totals.amount),
            "exposure": str(totals.exposure),
            "exceptions": [
                {"id": item.id, "amount": str(item.exception_amount), "exposure": str(item.estimated_exposure)}
                for item in exceptions
            ],
        },
    )
    await db.commit()
    await db.refresh(case)
    return case


@dataclass
class CaseAmountTotals:
    amount: Decimal
    exposure: Decimal


def validate_case_amount_review(body: CaseAmountReview, exceptions: list[DBException]) -> CaseAmountTotals:
    """Require a valid amount and exposure for every exception exactly once."""
    supplied = {item.exception_id: item for item in body.exceptions}
    if not exceptions or len(supplied) != len(body.exceptions) or set(supplied) != {item.id for item in exceptions}:
        raise HTTPException(status_code=422, detail="Provide each case exception exactly once")
    for item in body.exceptions:
        validate_reviewed_money(item.amount)
        validate_reviewed_money(item.exposure)
    amount = sum((item.amount for item in body.exceptions), Decimal("0"))
    exposure = sum((item.exposure for item in body.exceptions), Decimal("0"))
    if amount > MAX_MONEY or exposure > MAX_MONEY:
        raise HTTPException(status_code=422, detail="Case totals exceed supported monetary precision")
    return CaseAmountTotals(amount, exposure)


def validate_reviewed_money(value: Decimal) -> None:
    """Reject negative, non-finite, over-precision, or overflowing review amounts."""
    try:
        valid = value.is_finite() and 0 <= value <= MAX_MONEY and value == value.quantize(Decimal("0.01"))
    except InvalidOperation:
        valid = False
    if not valid:
        raise HTTPException(status_code=422, detail="Amounts must be non-negative and fit monetary precision")


def case_amount_snapshot(case: Case, exceptions: list[DBException]) -> dict:
    """Capture prior case and exception amounts for the audit event."""
    return {
        "amount": str(case.total_exception_amount),
        "exposure": str(case.total_exposure),
        "exceptions": [
            {"id": item.id, "amount": str(item.exception_amount), "exposure": str(item.estimated_exposure)}
            for item in exceptions
        ],
    }


def apply_case_amount_review(
    case: Case, exceptions: list[DBException], body: CaseAmountReview, totals: CaseAmountTotals
) -> None:
    """Apply the validated review values to locked case and exception rows."""
    supplied = {item.exception_id: item for item in body.exceptions}
    for item in exceptions:
        item.exception_amount = supplied[item.id].amount
        item.estimated_exposure = supplied[item.id].exposure
    case.total_exception_amount = totals.amount
    case.total_exposure = totals.exposure


@router.post("/api/cases/{case_id}/follow-up", response_model=CaseSchema)
async def follow_up_case(
    case_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance")),
):
    return await create_follow_up(db, case_id, user)
