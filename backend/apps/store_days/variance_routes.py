"""Variance tolerance rules, and explaining a store day's variance."""

import uuid
from decimal import Decimal

from database import get_db
from fastapi import APIRouter, Depends, HTTPException
from integrations.ontology.nimbus import audit
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.cases.models import Case
from apps.corrections.models import TransactionAdjustment
from apps.security import scope
from apps.security.authentication import require_role
from apps.store_days import variances
from apps.store_days.models import AuditTotal, VarianceExplanation, VarianceTolerance
from apps.store_days.service import get_day
from apps.transactions.models import CanonicalTransaction

tolerance_router = APIRouter(prefix="/api/variance-tolerances", tags=["Store days"])
explanation_router = APIRouter(prefix="/api/store-days", tags=["Store days"])


def blank_to_none(value: str | None) -> str | None:
    return (value.strip() or None) if isinstance(value, str) else value


class ToleranceBody(BaseModel):
    total_name: str | None = Field(default=None, max_length=100)
    location_trait: str | None = Field(default=None, max_length=100)
    within_amount: Decimal = Field(ge=0, max_digits=15, decimal_places=2)
    case_amount: Decimal = Field(ge=0, max_digits=15, decimal_places=2)

    @field_validator("total_name", "location_trait")
    @classmethod
    def strip_scope(cls, value: str | None) -> str | None:
        return blank_to_none(value)

    @model_validator(mode="after")
    def ordered(self):
        if self.case_amount < self.within_amount:
            raise ValueError("The case threshold cannot be below the tolerance")
        return self


class ExplanationBody(BaseModel):
    total_id: str = Field(min_length=1, max_length=36)
    reason: str = Field(min_length=1, max_length=2000)
    case_id: str | None = Field(default=None, max_length=36)
    correction_id: str | None = Field(default=None, max_length=36)

    @field_validator("case_id", "correction_id")
    @classmethod
    def strip_links(cls, value: str | None) -> str | None:
        return blank_to_none(value)

    @field_validator("reason")
    @classmethod
    def required(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A reason is required")
        return value.strip()


def tolerance_view(row: VarianceTolerance) -> dict:
    return {
        "id": row.id,
        "total_name": row.total_name,
        "location_trait": row.location_trait,
        "within_amount": row.within_amount,
        "case_amount": row.case_amount,
        "active": row.active,
        "updated_by": row.updated_by,
        "updated_at": row.updated_at,
    }


def describe(row: VarianceTolerance) -> str:
    return (
        f"{row.total_name or 'every total'} at {row.location_trait or 'every store'}: "
        f"within {row.within_amount}, case above {row.case_amount}"
    )


async def load_tolerance(db: AsyncSession, tolerance_id: str) -> VarianceTolerance:
    row = await db.get(VarianceTolerance, tolerance_id)
    if row is None or not row.active:
        raise HTTPException(status_code=404, detail="Tolerance rule not found")
    return row


async def require_unique_scope(
    db: AsyncSession, body: ToleranceBody, exclude: str | None = None
) -> None:
    clash = (
        (
            await db.execute(
                select(VarianceTolerance.id).where(
                    VarianceTolerance.active.is_(True),
                    VarianceTolerance.total_name.is_(None)
                    if body.total_name is None
                    else VarianceTolerance.total_name == body.total_name,
                    VarianceTolerance.location_trait.is_(None)
                    if body.location_trait is None
                    else VarianceTolerance.location_trait == body.location_trait,
                    VarianceTolerance.id != (exclude or ""),
                )
            )
        )
        .scalars()
        .first()
    )
    if clash:
        raise HTTPException(
            status_code=409, detail="A tolerance rule for that total and store group exists"
        )


@tolerance_router.get("")
async def list_tolerances(
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("finance", "it", "auditor")),
):
    rows = (
        (
            await db.execute(
                select(VarianceTolerance)
                .where(VarianceTolerance.active.is_(True))
                .order_by(VarianceTolerance.total_name, VarianceTolerance.location_trait)
            )
        )
        .scalars()
        .all()
    )
    return [tolerance_view(row) for row in rows]


@tolerance_router.post("")
async def create_tolerance(
    body: ToleranceBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance")),
):
    await require_unique_scope(db, body)
    row = VarianceTolerance(
        id=str(uuid.uuid4()), **body.model_dump(), updated_by=user["user"] or "ontology.user"
    )
    db.add(row)
    audit(
        db,
        "VARIANCE_TOLERANCE_CREATED",
        None,
        "VarianceTolerance",
        row.id,
        f"Variance tolerance set for {describe(row)}",
        actor=row.updated_by,
        after={key: str(value) for key, value in body.model_dump().items()},
    )
    await db.commit()
    return tolerance_view(row)


@tolerance_router.put("/{tolerance_id}")
async def update_tolerance(
    tolerance_id: str,
    body: ToleranceBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance")),
):
    row = await load_tolerance(db, tolerance_id)
    await require_unique_scope(db, body, exclude=row.id)
    before = describe(row)
    for key, value in body.model_dump().items():
        setattr(row, key, value)
    row.updated_by = user["user"] or "ontology.user"
    audit(
        db,
        "VARIANCE_TOLERANCE_UPDATED",
        None,
        "VarianceTolerance",
        row.id,
        f"Variance tolerance changed from {before} to {describe(row)}",
        actor=row.updated_by,
        after={key: str(value) for key, value in body.model_dump().items()},
    )
    await db.commit()
    return tolerance_view(row)


@tolerance_router.delete("/{tolerance_id}")
async def delete_tolerance(
    tolerance_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance")),
):
    row = await load_tolerance(db, tolerance_id)
    row.active, row.updated_by = False, user["user"] or "ontology.user"
    audit(
        db,
        "VARIANCE_TOLERANCE_REMOVED",
        None,
        "VarianceTolerance",
        row.id,
        f"Variance tolerance removed for {describe(row)}",
        actor=row.updated_by,
    )
    await db.commit()
    return {"id": row.id, "active": False}


@explanation_router.post("/{store_id}/{business_date}/variance-explanations")
async def explain_variance(
    store_id: str,
    business_date: str,
    body: ExplanationBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it")),
):
    """Record why a total carries a variance, and the case or correction that resolves it."""
    scope.require_store(user, store_id)
    day = await get_day(db, store_id, business_date, lock=True)
    if day.status == "Closed":
        raise HTTPException(status_code=409, detail="Reopen the store day to explain a variance")
    total = await db.get(AuditTotal, body.total_id)
    if total is None or total.store_day_id != day.id or total.audit_version != day.audit_version:
        raise HTTPException(
            status_code=404, detail="Total not found in this store day's current version"
        )
    evaluation = (await variances.evaluate(db, day, [total]))[total.id]
    needed = evaluation["outcome"]
    if needed not in (variances.NEEDS_REASON, variances.NEEDS_CASE):
        raise HTTPException(
            status_code=422, detail=f"This variance needs no explanation ({needed or 'none'})"
        )
    if needed == variances.NEEDS_CASE and not (body.case_id or body.correction_id):
        raise HTTPException(
            status_code=422,
            detail="This variance is above the case threshold; link the case or correction",
        )
    if body.case_id:
        case = await db.get(Case, body.case_id)
        if case is None or (case.store_id, case.business_date) != (store_id, business_date):
            raise HTTPException(status_code=422, detail="Link a case raised for this store day")
    if body.correction_id:
        correction = await db.get(TransactionAdjustment, body.correction_id)
        if correction is None or correction.status == "Rejected":
            raise HTTPException(status_code=422, detail="Link a correction that is not rejected")
        transaction = (
            await db.get(CanonicalTransaction, correction.transaction_id)
            if correction.transaction_id
            else None
        )
        if transaction is None or (transaction.store_id, transaction.business_date) != (
            store_id,
            business_date,
        ):
            raise HTTPException(
                status_code=422, detail="Link a correction to a transaction of this store day"
            )
    actor = user["user"] or "ontology.user"
    row = VarianceExplanation(
        id=str(uuid.uuid4()),
        store_day_id=day.id,
        audit_total_id=total.id,
        audit_version=total.audit_version,
        total_name=total.total_name,
        level=total.level,
        dimension_key=total.dimension_key,
        variance_amount=total.variance_amount,
        outcome=needed,
        reason=body.reason,
        case_id=body.case_id,
        correction_id=body.correction_id,
        explained_by=actor,
    )
    db.add(row)
    audit(
        db,
        "VARIANCE_EXPLAINED",
        body.case_id,
        "StoreDay",
        day.id,
        f"Variance of {total.variance_amount} on {total.total_name} explained: {body.reason}",
        actor=actor,
        after={
            "total": total.total_name,
            "dimension": total.dimension_key,
            "variance_amount": str(total.variance_amount),
            "outcome": needed,
            "case_id": body.case_id,
            "correction_id": body.correction_id,
        },
    )
    await db.commit()
    return variances.explanation_view(row)
