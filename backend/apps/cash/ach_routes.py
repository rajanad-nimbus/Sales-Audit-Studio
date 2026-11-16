"""Bank and store ACH adjustment ledger for next-day settlement review."""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal

from database import get_db
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.audit.models import AuditEvent
from apps.cash.models import ACHAdjustment
from apps.csv_export import csv_row
from apps.ingest.manifests import validate_business_date
from apps.security import scope
from apps.security.authentication import require_role
from apps.totals.currency import validate_currency

router = APIRouter(prefix="/api/ach", tags=["ACH"])
MAX_ACH_EXPORT = 50_000


class ACHAdjustmentBody(BaseModel):
    record_type: Literal["Bank", "Store"]
    store_id: str | None = Field(default=None, max_length=50)
    bank_id: str = Field(min_length=1, max_length=50)
    account_reference: str | None = Field(default=None, max_length=100)
    business_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    currency: str = Field(default="USD", min_length=3, max_length=3)
    adjustment_amount: Decimal = Field(max_digits=15, decimal_places=2)
    note: str = Field(min_length=1, max_length=500)

    @field_validator("business_date")
    @classmethod
    def valid_date(cls, value: str) -> str:
        try:
            return validate_business_date(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("store_id", "account_reference")
    @classmethod
    def normalize_optional(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @field_validator("bank_id", "note")
    @classmethod
    def normalize_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Value cannot be blank")
        return value

    @field_validator("currency")
    @classmethod
    def valid_currency(cls, value: str) -> str:
        try:
            return validate_currency(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc


def view(row: ACHAdjustment) -> dict:
    return {
        "id": row.id,
        "record_type": row.record_type,
        "store_id": row.store_id,
        "bank_id": row.bank_id,
        "account_reference": row.account_reference,
        "business_date": row.business_date,
        "currency": row.currency,
        "adjustment_amount": row.adjustment_amount,
        "note": row.note,
        "status": row.status,
        "created_by": row.created_by,
        "created_at": row.created_at,
        "reversed_by": row.reversed_by,
        "reversed_at": row.reversed_at,
    }


@router.get("/adjustments")
async def list_adjustments(
    record_type: Annotated[Literal["Bank", "Store"] | None, Query()] = None,
    store_id: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
    bank_id: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
    business_date: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}-\d{2}$")] = None,
    status: Annotated[Literal["Active", "Reversed"] | None, Query()] = None,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it")),
    format: Literal["json", "csv"] = "json",
):
    if scope.scoped(user) and record_type == "Bank":
        raise HTTPException(status_code=403, detail="Bank-wide ACH records require HQ access")
    filters = []
    if record_type:
        filters.append(ACHAdjustment.record_type == record_type)
    if store_id:
        filters.append(ACHAdjustment.store_id == store_id)
    if bank_id:
        filters.append(ACHAdjustment.bank_id == bank_id)
    if business_date:
        try:
            validate_business_date(business_date)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        filters.append(ACHAdjustment.business_date == business_date)
    if status:
        filters.append(ACHAdjustment.status == status)
    query = scope.restrict(select(ACHAdjustment).where(*filters), user, ACHAdjustment.store_id)
    ordering = (
        ACHAdjustment.business_date.desc(),
        ACHAdjustment.created_at.desc(),
        ACHAdjustment.id,
    )
    if format == "csv":
        total = (
            await db.execute(select(func.count()).select_from(query.subquery()))
        ).scalar_one()
        if total > MAX_ACH_EXPORT:
            raise HTTPException(
                status_code=413,
                detail=f"Export contains {total:,} ACH adjustments; limit is {MAX_ACH_EXPORT:,}. Narrow the filters.",
            )
        columns = (
            "record_type",
            "store_id",
            "bank_id",
            "account_reference",
            "business_date",
            "currency",
            "adjustment_amount",
            "note",
            "status",
            "created_by",
            "created_at",
            "reversed_by",
            "reversed_at",
        )

        async def generate():
            yield csv_row(columns)
            result = await db.stream(query.order_by(*ordering).execution_options(yield_per=1000))
            try:
                async for row in result.scalars():
                    item = view(row)
                    yield csv_row(item[column] for column in columns)
            finally:
                await result.close()

        return StreamingResponse(
            generate(),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="ach-adjustments.csv"'},
        )
    rows = (
        (
            await db.execute(
                query.order_by(*ordering).offset(skip).limit(limit + 1)
            )
        )
        .scalars()
        .all()
    )
    return {"items": [view(row) for row in rows[:limit]], "has_more": len(rows) > limit}


@router.get("/settlement-summary")
async def settlement_summary(
    record_type: Annotated[Literal["Bank", "Store"], Query()],
    business_date: Annotated[str, Query(pattern=r"^\d{4}-\d{2}-\d{2}$")],
    store_id: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
    bank_id: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it")),
):
    """Group active manual adjustments by their next-day settlement destination."""
    try:
        business_date = validate_business_date(business_date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if record_type == "Bank" and scope.scoped(user):
        raise HTTPException(status_code=403, detail="Bank-wide ACH records require HQ access")
    if record_type == "Bank" and store_id:
        raise HTTPException(status_code=422, detail="Bank ACH summaries cannot be filtered by store")

    filters = [
        ACHAdjustment.record_type == record_type,
        ACHAdjustment.business_date == business_date,
        ACHAdjustment.status == "Active",
    ]
    if store_id:
        filters.append(ACHAdjustment.store_id == store_id)
    if bank_id:
        filters.append(ACHAdjustment.bank_id == bank_id)
    query = (
        select(
            ACHAdjustment.bank_id,
            ACHAdjustment.store_id,
            ACHAdjustment.account_reference,
            ACHAdjustment.currency,
            func.sum(ACHAdjustment.adjustment_amount),
            func.count(ACHAdjustment.id),
        )
        .where(*filters)
        .group_by(
            ACHAdjustment.bank_id,
            ACHAdjustment.store_id,
            ACHAdjustment.account_reference,
            ACHAdjustment.currency,
        )
        .order_by(
            ACHAdjustment.store_id,
            ACHAdjustment.bank_id,
            ACHAdjustment.account_reference,
            ACHAdjustment.currency,
        )
    )
    query = scope.restrict(query, user, ACHAdjustment.store_id)
    rows = (
        await db.execute(query)
    ).all()
    settlement_date = (date.fromisoformat(business_date) + timedelta(days=1)).isoformat()
    return {
        "record_type": record_type,
        "business_date": business_date,
        "settlement_date": settlement_date,
        "items": [
            {
                "bank_id": bank_id,
                "store_id": store,
                "account_reference": account_reference,
                "currency": currency,
                "net_adjustment": amount,
                "active_adjustments": count,
            }
            for bank_id, store, account_reference, currency, amount, count in rows
        ],
    }


@router.post("/adjustments", status_code=201)
async def add_adjustment(
    body: ACHAdjustmentBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance")),
):
    if scope.scoped(user) and body.record_type == "Bank":
        raise HTTPException(status_code=403, detail="Bank-wide ACH records require HQ access")
    if body.record_type == "Store" and not body.store_id:
        raise HTTPException(status_code=422, detail="A store is required for a store adjustment")
    if body.record_type == "Store":
        scope.require_store(user, body.store_id)
    if body.record_type == "Bank" and body.store_id:
        raise HTTPException(
            status_code=422, detail="A bank adjustment cannot be assigned to a store"
        )
    row = ACHAdjustment(
        id=str(uuid.uuid4()), **body.model_dump(), created_by=user.get("user") or "ontology.user"
    )
    db.add(row)
    db.add(
        AuditEvent(
            id=str(uuid.uuid4()),
            event_type="ACH_ADJUSTMENT_ADDED",
            actor=row.created_by,
            object_type="ACHAdjustment",
            object_id=row.id,
            action_description=f"{row.record_type} ACH adjustment added for {row.bank_id}/{row.business_date}",
            after_state={
                **body.model_dump(mode="json"),
                "adjustment_amount": str(body.adjustment_amount),
            },
        )
    )
    await db.commit()
    await db.refresh(row)
    return view(row)


@router.post("/adjustments/{adjustment_id}/reverse")
async def reverse_adjustment(
    adjustment_id: str,
    reason: Annotated[str, Query(min_length=3, max_length=500)],
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance")),
):
    row = (
        await db.execute(
            select(ACHAdjustment).where(ACHAdjustment.id == adjustment_id).with_for_update()
        )
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="ACH adjustment not found")
    if scope.scoped(user) and row.record_type == "Bank":
        raise HTTPException(status_code=403, detail="Bank-wide ACH records require HQ access")
    if row.record_type == "Store":
        scope.require_store(user, row.store_id)
    if row.status != "Active":
        raise HTTPException(status_code=409, detail="ACH adjustment has already been reversed")
    reason = reason.strip()
    if len(reason) < 3:
        raise HTTPException(status_code=422, detail="Reason must contain at least three characters")
    actor = user.get("user") or "ontology.user"
    row.status = "Reversed"
    row.reversed_by = actor
    row.reversed_at = datetime.now(UTC)
    db.add(
        AuditEvent(
            id=str(uuid.uuid4()),
            event_type="ACH_ADJUSTMENT_REVERSED",
            actor=actor,
            object_type="ACHAdjustment",
            object_id=row.id,
            action_description=f"ACH adjustment reversed: {reason}",
            before_state={"status": "Active", "adjustment_amount": str(row.adjustment_amount)},
            after_state={"status": "Reversed", "reason": reason},
        )
    )
    await db.commit()
    await db.refresh(row)
    return view(row)
