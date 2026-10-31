"""Cash-office declarations and over/short reconciliation."""
import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.security import scope
from apps.corrections.effects import sum_effective_amounts
from apps.database_errors import violates_unique_constraint
from apps.store_days.freshness import lock_store_day
from apps.security.authentication import require_role
from database import get_db
from apps.transactions.models import CanonicalTransaction
from apps.cash.models import CashControl, CashDeposit
from integrations.ontology.nimbus import audit

router = APIRouter(prefix="/api/cash-controls", tags=["Cash office"])
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500

class CashDepositBody(BaseModel):
    store_id: str = Field(min_length=1, max_length=50)
    business_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    deposit_reference: str = Field(min_length=1, max_length=100)
    deposited_amount: Decimal = Field(ge=0, max_digits=15, decimal_places=2)
    bank_account: str | None = Field(default=None, max_length=100)

    @field_validator("business_date")
    @classmethod
    def validate_business_date(cls, value: str) -> str:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError("business_date must be a valid YYYY-MM-DD date")
        return value

    @field_validator("deposit_reference")
    @classmethod
    def validate_deposit_reference(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("deposit_reference cannot be blank")
        return value.strip()

    @field_validator("store_id")
    @classmethod
    def normalize_store_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("store_id cannot be blank")
        return value.strip()

    @field_validator("bank_account")
    @classmethod
    def normalize_bank_account(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

class CashControlBody(BaseModel):
    store_id: str = Field(min_length=1, max_length=50)
    business_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    register_id: str | None = Field(default=None, max_length=50)
    cashier_id: str | None = Field(default=None, max_length=100)
    tender_type: str = Field(default="Cash", min_length=1, max_length=50)
    declared_cash: Decimal = Field(default=Decimal("0"), ge=0, max_digits=15, decimal_places=2)
    paid_out: Decimal = Field(default=Decimal("0"), ge=0, max_digits=15, decimal_places=2)
    safe_drop: Decimal = Field(default=Decimal("0"), ge=0, max_digits=15, decimal_places=2)

    @field_validator("business_date")
    @classmethod
    def validate_business_date(cls, value: str) -> str:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError("business_date must be a valid YYYY-MM-DD date")
        return value

    @field_validator("store_id")
    @classmethod
    def normalize_store_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("store_id cannot be blank")
        return value.strip()

    @field_validator("register_id", "cashier_id")
    @classmethod
    def normalize_optional_identifier(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @field_validator("tender_type")
    @classmethod
    def normalize_tender_type(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("tender_type cannot be blank")
        return value.strip()

def view(row: CashControl):
    return {"id": row.id, "store_id": row.store_id, "business_date": row.business_date, "register_id": row.register_id, "cashier_id": row.cashier_id, "tender_type": row.tender_type, "declared_cash": row.declared_cash, "paid_out": row.paid_out, "safe_drop": row.safe_drop, "expected_amount": row.expected_amount, "variance_amount": row.variance_amount, "status": row.status, "declared_by": row.declared_by, "updated_at": row.updated_at}
def deposit_view(row: CashDeposit):
    return {"id": row.id, "store_id": row.store_id, "business_date": row.business_date, "deposit_reference": row.deposit_reference, "bank_account": row.bank_account, "deposited_amount": row.deposited_amount, "expected_amount": row.expected_amount, "variance_amount": row.variance_amount, "status": row.status, "recorded_by": row.recorded_by, "created_at": row.created_at}

async def calculate(db: AsyncSession, row: CashControl):
    query = select(CanonicalTransaction).where(
        CanonicalTransaction.store_id == row.store_id,
        CanonicalTransaction.business_date == row.business_date,
        CanonicalTransaction.tender_type == row.tender_type,
    )
    if row.register_id:
        query = query.where(CanonicalTransaction.register_id == row.register_id)
    if row.cashier_id:
        query = query.where(CanonicalTransaction.cashier_id == row.cashier_id)
    expected = await sum_effective_amounts(db, query)
    accounted = row.declared_cash + row.paid_out + row.safe_drop
    row.expected_amount, row.variance_amount = expected, expected - accounted
    row.status = "Balanced" if row.variance_amount == 0 else "Over/Short"


async def expected_cash_deposit(db: AsyncSession, store_id: str, business_date: str) -> Decimal:
    """Sum the most detailed non-overlapping cash declarations for a store day."""
    day_scope = (
        CashControl.store_id == store_id,
        CashControl.business_date == business_date,
        CashControl.tender_type == "Cash",
    )
    amount = CashControl.declared_cash + CashControl.safe_drop

    store_total = (
        await db.execute(
            select(func.sum(amount)).where(
                *day_scope,
                CashControl.register_id.is_(None),
                CashControl.cashier_id.is_(None),
            )
        )
    ).scalar_one()
    if store_total is not None:
        return store_total

    register_rows = (
        await db.execute(
            select(CashControl.register_id, func.sum(amount))
            .where(
                *day_scope,
                CashControl.register_id.is_not(None),
                CashControl.cashier_id.is_(None),
            )
            .group_by(CashControl.register_id)
        )
    ).all()
    register_totals = {register_id: total for register_id, total in register_rows}

    cashier_query = select(func.sum(amount)).where(
        *day_scope,
        CashControl.cashier_id.is_not(None),
    )
    if register_totals:
        cashier_query = cashier_query.where(
            or_(
                CashControl.register_id.is_(None),
                and_(
                    CashControl.register_id.is_not(None),
                    CashControl.register_id.not_in(register_totals),
                ),
            )
        )
    cashier_total = (await db.execute(cashier_query)).scalar_one() or Decimal("0")
    return sum(register_totals.values(), Decimal("0")) + cashier_total


async def require_writable_store_day(db: AsyncSession, store_id: str, business_date: str) -> None:
    """Require an explicitly opened, non-closed store day for cash-office writes."""
    day = await lock_store_day(db, store_id, business_date)
    if day is None:
        raise HTTPException(status_code=409, detail="Open the store day before recording cash controls")
    if day.status == "Closed":
        raise HTTPException(status_code=409, detail="Reopen the store day with a rationale before changing cash controls")

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
    total_query = scope.restrict(select(func.count()).select_from(CashControl).where(*filters), user, CashControl.store_id)
    total = (await db.execute(total_query)).scalar_one()
    rows = (await db.execute(query.order_by(CashControl.business_date.desc(), CashControl.store_id,
                                           CashControl.id).offset(skip).limit(limit))).scalars().all()
    _set_page_headers(response, total, skip, len(rows))
    return [view(row) for row in rows]

@router.post("")
async def declare_cash(body: CashControlBody, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance", "it"))):
    scope.require_store(user, body.store_id)
    if min(body.declared_cash, body.paid_out, body.safe_drop) < 0: raise HTTPException(status_code=422, detail="Cash amounts must be non-negative")
    await require_writable_store_day(db, body.store_id, body.business_date)
    row = CashControl(id=str(uuid.uuid4()), **body.model_dump(), declared_by=user["user"] or "ontology.user")
    db.add(row)
    await db.flush()
    await calculate(db, row)
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
    total_query = scope.restrict(select(func.count()).select_from(CashDeposit).where(*filters), user, CashDeposit.store_id)
    total = (await db.execute(total_query)).scalar_one()
    rows = (await db.execute(query.order_by(CashDeposit.business_date.desc(), CashDeposit.store_id,
                                           CashDeposit.id).offset(skip).limit(limit))).scalars().all()
    _set_page_headers(response, total, skip, len(rows))
    return [deposit_view(row) for row in rows]


def _validate_date_filter(value: str) -> None:
    try:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="business_date must be a valid YYYY-MM-DD date") from exc


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
async def record_deposit(body: CashDepositBody, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance", "it"))):
    scope.require_store(user, body.store_id)
    if body.deposited_amount < 0 or not body.deposit_reference.strip(): raise HTTPException(status_code=422, detail="Deposit amount must be non-negative and reference is required")
    await require_writable_store_day(db, body.store_id, body.business_date)
    expected = await expected_cash_deposit(db, body.store_id, body.business_date)
    variance = expected - body.deposited_amount
    row = CashDeposit(id=str(uuid.uuid4()), **body.model_dump(), expected_amount=expected, variance_amount=variance, status="Balanced" if variance == 0 else "Over/Short", recorded_by=user["user"] or "ontology.user")
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
    await db.refresh(row); return deposit_view(row)
