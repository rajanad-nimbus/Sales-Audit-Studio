"""Cash declaration calculations and store-day write rules."""

from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.cash.models import CashControl, CashDeposit
from apps.corrections.effects import sum_effective_amounts
from apps.store_days.freshness import lock_store_day
from apps.totals.currency import single_currency
from apps.transactions.models import CanonicalTransaction


async def calculate_declaration(db: AsyncSession, declaration: CashControl) -> None:
    """Reconcile one declaration to effective transaction amounts in its scope."""
    await ensure_store_day_currency(db, declaration.store_id, declaration.business_date)
    query = select(CanonicalTransaction).where(
        CanonicalTransaction.store_id == declaration.store_id,
        CanonicalTransaction.business_date == declaration.business_date,
        CanonicalTransaction.tender_type == declaration.tender_type,
    )
    if declaration.register_id:
        query = query.where(CanonicalTransaction.register_id == declaration.register_id)
    if declaration.cashier_id:
        query = query.where(CanonicalTransaction.cashier_id == declaration.cashier_id)

    expected = await sum_effective_amounts(db, query)
    accounted = declaration.declared_cash + declaration.paid_out + declaration.safe_drop
    declaration.expected_amount = expected
    declaration.variance_amount = expected - accounted
    declaration.status = "Balanced" if declaration.variance_amount == 0 else "Over/Short"


async def expected_cash_deposit(
    db: AsyncSession,
    store_id: str,
    business_date: str,
) -> Decimal:
    """Sum the most detailed non-overlapping cash declarations for a store day."""
    await ensure_store_day_currency(db, store_id, business_date)
    day_scope = (
        CashControl.store_id == store_id,
        CashControl.business_date == business_date,
        CashControl.tender_type == "Cash",
    )
    amount = CashControl.declared_cash + CashControl.safe_drop
    scope_totals = (
        await db.execute(
            select(
                CashControl.register_id,
                CashControl.cashier_id,
                func.sum(amount),
            )
            .where(*day_scope)
            .group_by(CashControl.register_id, CashControl.cashier_id)
        )
    ).all()

    store_totals = [
        total
        for register_id, cashier_id, total in scope_totals
        if register_id is None and cashier_id is None
    ]
    if store_totals:
        return sum(store_totals, Decimal("0"))

    register_totals = {
        register_id: total
        for register_id, cashier_id, total in scope_totals
        if register_id is not None and cashier_id is None
    }
    cashier_totals = [
        total
        for register_id, cashier_id, total in scope_totals
        if cashier_id is not None and (register_id is None or register_id not in register_totals)
    ]
    return sum(register_totals.values(), Decimal("0")) + sum(cashier_totals, Decimal("0"))


async def ensure_store_day_currency(
    db: AsyncSession,
    store_id: str,
    business_date: str,
) -> str:
    """Reject cash calculations when a store day contains incompatible currencies."""
    currencies = (await db.execute(
        select(CanonicalTransaction.currency)
        .where(
            CanonicalTransaction.store_id == store_id,
            CanonicalTransaction.business_date == business_date,
        )
        .distinct()
    )).scalars().all()
    try:
        return single_currency(currencies)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


async def refresh_cash_deposits(
    db: AsyncSession,
    store_id: str,
    business_date: str,
) -> list[CashDeposit]:
    """Reconcile recorded deposits to the latest cash declarations for an open day."""
    deposits = (await db.execute(
        select(CashDeposit)
        .where(CashDeposit.store_id == store_id, CashDeposit.business_date == business_date)
        .order_by(CashDeposit.id)
        .with_for_update()
    )).scalars().all()
    if not deposits:
        return []

    expected = await expected_cash_deposit(db, store_id, business_date)
    changed = []
    for deposit in deposits:
        variance = expected - deposit.deposited_amount
        status = "Balanced" if variance == 0 else "Over/Short"
        if (deposit.expected_amount, deposit.variance_amount, deposit.status) == (expected, variance, status):
            continue
        deposit.expected_amount = expected
        deposit.variance_amount = variance
        deposit.status = status
        changed.append(deposit)
    return changed


async def require_writable_store_day(
    db: AsyncSession,
    store_id: str,
    business_date: str,
) -> None:
    """Lock an open store day before a cash-office write."""
    day = await lock_store_day(db, store_id, business_date)
    if day is None:
        raise HTTPException(
            status_code=409,
            detail="Open the store day before recording cash controls",
        )
    if day.status == "Closed":
        raise HTTPException(
            status_code=409,
            detail="Reopen the store day with a rationale before changing cash controls",
        )
