"""Cash-office declarations and over/short reconciliation."""

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.security import scope
from apps.database_errors import violates_unique_constraint
from apps.security.authentication import require_role
from database import get_db
from apps.cash.models import CashControl, CashDeposit
from apps.cash.schemas import CashControlBody, CashDepositBody
from apps.cash.service import (
    calculate_declaration,
    expected_cash_deposit,
    require_writable_store_day,
)
from integrations.ontology.nimbus import audit

router = APIRouter(prefix="/api/cash-controls", tags=["Cash office"])
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500


def view(row: CashControl):
    return {
        "id": row.id,
        "store_id": row.store_id,
        "business_date": row.business_date,
        "register_id": row.register_id,
        "cashier_id": row.cashier_id,
        "tender_type": row.tender_type,
        "declared_cash": row.declared_cash,
        "paid_out": row.paid_out,
        "safe_drop": row.safe_drop,
        "expected_amount": row.expected_amount,
        "variance_amount": row.variance_amount,
        "status": row.status,
        "declared_by": row.declared_by,
        "updated_at": row.updated_at,
    }


def deposit_view(row: CashDeposit):
    return {
        "id": row.id,
        "store_id": row.store_id,
        "business_date": row.business_date,
        "deposit_reference": row.deposit_reference,
        "bank_account": row.bank_account,
        "deposited_amount": row.deposited_amount,
        "expected_amount": row.expected_amount,
        "variance_amount": row.variance_amount,
        "status": row.status,
        "recorded_by": row.recorded_by,
        "created_at": row.created_at,
    }


@router.get("")
async def list_controls(
    store_id: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
    business_date: Annotated[str | None, Query(min_length=10, max_length=10)] = None,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it")),
    response: Response = None,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
):
    filters = []
    if store_id:
        filters.append(CashControl.store_id == store_id)
    if business_date:
        _validate_date_filter(business_date)
        filters.append(CashControl.business_date == business_date)
    query = scope.restrict(select(CashControl).where(*filters), user, CashControl.store_id)
    total_query = scope.restrict(
        select(func.count()).select_from(CashControl).where(*filters), user, CashControl.store_id
    )
    total = (await db.execute(total_query)).scalar_one()
    rows = (
        (
            await db.execute(
                query.order_by(
                    CashControl.business_date.desc(), CashControl.store_id, CashControl.id
                )
                .offset(skip)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    _set_page_headers(response, total, skip, len(rows))
    return [view(row) for row in rows]


@router.post("")
async def declare_cash(
    body: CashControlBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it")),
):
    scope.require_store(user, body.store_id)
    if min(body.declared_cash, body.paid_out, body.safe_drop) < 0:
        raise HTTPException(status_code=422, detail="Cash amounts must be non-negative")
    await require_writable_store_day(db, body.store_id, body.business_date)
    row = CashControl(
        id=str(uuid.uuid4()), **body.model_dump(), declared_by=user["user"] or "ontology.user"
    )
    db.add(row)
    await db.flush()
    await calculate_declaration(db, row)
    audit(
        db,
        "CASH_CONTROL_DECLARED",
        None,
        "CashControl",
        row.id,
        f"Cash control declared for {row.store_id}/{row.business_date}",
        actor=row.declared_by,
        after={
            "store_id": row.store_id,
            "business_date": row.business_date,
            "register_id": row.register_id,
            "cashier_id": row.cashier_id,
            "tender_type": row.tender_type,
            "declared_cash": str(row.declared_cash),
            "paid_out": str(row.paid_out),
            "safe_drop": str(row.safe_drop),
            "expected_amount": str(row.expected_amount),
            "variance_amount": str(row.variance_amount),
            "status": row.status,
        },
    )
    await db.commit()
    await db.refresh(row)
    return view(row)


@router.get("/deposits")
async def list_deposits(
    store_id: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
    business_date: Annotated[str | None, Query(min_length=10, max_length=10)] = None,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it")),
    response: Response = None,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
):
    filters = []
    if store_id:
        filters.append(CashDeposit.store_id == store_id)
    if business_date:
        _validate_date_filter(business_date)
        filters.append(CashDeposit.business_date == business_date)
    query = scope.restrict(select(CashDeposit).where(*filters), user, CashDeposit.store_id)
    total_query = scope.restrict(
        select(func.count()).select_from(CashDeposit).where(*filters), user, CashDeposit.store_id
    )
    total = (await db.execute(total_query)).scalar_one()
    rows = (
        (
            await db.execute(
                query.order_by(
                    CashDeposit.business_date.desc(), CashDeposit.store_id, CashDeposit.id
                )
                .offset(skip)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    _set_page_headers(response, total, skip, len(rows))
    return [deposit_view(row) for row in rows]


def _validate_date_filter(value: str) -> None:
    try:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail="business_date must be a valid YYYY-MM-DD date"
        ) from exc


def _set_page_headers(response: Response, total: int, skip: int, returned: int) -> None:
    if response is None:
        return
    response.headers["X-Total-Count"] = str(total)
    response.headers["X-Has-More"] = str(skip + returned < total).lower()


def _is_duplicate_deposit_reference(exc: IntegrityError) -> bool:
    """Recognize only the deposit-reference unique constraint across supported databases."""
    return violates_unique_constraint(
        exc,
        constraint_names={"cash_deposits_deposit_reference_key"},
        sqlite_messages={"UNIQUE constraint failed: cash_deposits.deposit_reference"},
    )


@router.post("/deposits")
async def record_deposit(
    body: CashDepositBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it")),
):
    scope.require_store(user, body.store_id)
    if body.deposited_amount < 0 or not body.deposit_reference.strip():
        raise HTTPException(
            status_code=422, detail="Deposit amount must be non-negative and reference is required"
        )
    await require_writable_store_day(db, body.store_id, body.business_date)
    expected = await expected_cash_deposit(db, body.store_id, body.business_date)
    variance = expected - body.deposited_amount
    row = CashDeposit(
        id=str(uuid.uuid4()),
        **body.model_dump(),
        expected_amount=expected,
        variance_amount=variance,
        status="Balanced" if variance == 0 else "Over/Short",
        recorded_by=user["user"] or "ontology.user",
    )
    db.add(row)
    audit(
        db,
        "CASH_DEPOSIT_RECORDED",
        None,
        "CashDeposit",
        row.id,
        f"Cash deposit recorded for {row.store_id}/{row.business_date}",
        actor=row.recorded_by,
        after={
            "store_id": row.store_id,
            "business_date": row.business_date,
            "deposit_reference": row.deposit_reference,
            "deposited_amount": str(row.deposited_amount),
            "expected_amount": str(row.expected_amount),
            "variance_amount": str(row.variance_amount),
            "status": row.status,
        },
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if _is_duplicate_deposit_reference(exc):
            raise HTTPException(status_code=409, detail="Deposit reference already exists") from exc
        raise
    await db.refresh(row)
    return deposit_view(row)
