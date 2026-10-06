"""Cash-office declarations and over/short reconciliation."""
import uuid
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import scope
from auth import require_role
from database import get_db
from models import CanonicalTransaction, CashControl, CashDeposit

router = APIRouter(prefix="/api/cash-controls", tags=["Cash office"])

class CashDepositBody(BaseModel):
    store_id: str
    business_date: str
    deposit_reference: str
    deposited_amount: Decimal
    bank_account: str | None = None

class CashControlBody(BaseModel):
    store_id: str
    business_date: str
    register_id: str | None = None
    cashier_id: str | None = None
    tender_type: str = "Cash"
    declared_cash: Decimal = Decimal("0")
    paid_out: Decimal = Decimal("0")
    safe_drop: Decimal = Decimal("0")

def view(row: CashControl):
    return {"id": row.id, "store_id": row.store_id, "business_date": row.business_date, "register_id": row.register_id, "cashier_id": row.cashier_id, "tender_type": row.tender_type, "declared_cash": row.declared_cash, "paid_out": row.paid_out, "safe_drop": row.safe_drop, "expected_amount": row.expected_amount, "variance_amount": row.variance_amount, "status": row.status, "declared_by": row.declared_by, "updated_at": row.updated_at}
def deposit_view(row: CashDeposit):
    return {"id": row.id, "store_id": row.store_id, "business_date": row.business_date, "deposit_reference": row.deposit_reference, "bank_account": row.bank_account, "deposited_amount": row.deposited_amount, "expected_amount": row.expected_amount, "variance_amount": row.variance_amount, "status": row.status, "recorded_by": row.recorded_by, "created_at": row.created_at}

async def calculate(db: AsyncSession, row: CashControl):
    query = select(CanonicalTransaction).where(CanonicalTransaction.store_id == row.store_id, CanonicalTransaction.business_date == row.business_date, CanonicalTransaction.tender_type == row.tender_type)
    if row.register_id: query = query.where(CanonicalTransaction.register_id == row.register_id)
    transactions = (await db.execute(query)).scalars().all()
    expected = sum((transaction.signed_amount for transaction in transactions), Decimal("0"))
    accounted = row.declared_cash + row.paid_out + row.safe_drop
    row.expected_amount, row.variance_amount = expected, expected - accounted
    row.status = "Balanced" if row.variance_amount == 0 else "Over/Short"

@router.get("")
async def list_controls(store_id: str | None = None, business_date: str | None = None, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance", "it"))):
    query = scope.restrict(select(CashControl), user, CashControl.store_id).order_by(CashControl.business_date.desc(), CashControl.store_id)
    if store_id: query = query.where(CashControl.store_id == store_id)
    if business_date: query = query.where(CashControl.business_date == business_date)
    return [view(row) for row in (await db.execute(query)).scalars().all()]

@router.post("")
async def declare_cash(body: CashControlBody, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance", "it"))):
    scope.require_store(user, body.store_id)
    if min(body.declared_cash, body.paid_out, body.safe_drop) < 0: raise HTTPException(status_code=422, detail="Cash amounts must be non-negative")
    row = CashControl(id=str(uuid.uuid4()), **body.model_dump(), declared_by=user["user"] or "ontology.user")
    db.add(row); await db.flush(); await calculate(db, row); await db.commit(); await db.refresh(row)
    return view(row)

@router.get("/deposits")
async def list_deposits(store_id: str | None = None, business_date: str | None = None, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance", "it"))):
    query = scope.restrict(select(CashDeposit), user, CashDeposit.store_id).order_by(CashDeposit.business_date.desc(), CashDeposit.store_id)
    if store_id: query = query.where(CashDeposit.store_id == store_id)
    if business_date: query = query.where(CashDeposit.business_date == business_date)
    return [deposit_view(row) for row in (await db.execute(query)).scalars().all()]

@router.post("/deposits")
async def record_deposit(body: CashDepositBody, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance", "it"))):
    scope.require_store(user, body.store_id)
    if body.deposited_amount < 0 or not body.deposit_reference.strip(): raise HTTPException(status_code=422, detail="Deposit amount must be non-negative and reference is required")
    controls = (await db.execute(select(CashControl).where(CashControl.store_id == body.store_id, CashControl.business_date == body.business_date, CashControl.tender_type == "Cash"))).scalars().all()
    expected = sum((row.declared_cash + row.safe_drop for row in controls), Decimal("0"))
    variance = expected - body.deposited_amount
    row = CashDeposit(id=str(uuid.uuid4()), **body.model_dump(), expected_amount=expected, variance_amount=variance, status="Balanced" if variance == 0 else "Over/Short", recorded_by=user["user"] or "ontology.user")
    db.add(row)
    try: await db.commit()
    except Exception: await db.rollback(); raise HTTPException(status_code=409, detail="Deposit reference already exists")
    await db.refresh(row); return deposit_view(row)
