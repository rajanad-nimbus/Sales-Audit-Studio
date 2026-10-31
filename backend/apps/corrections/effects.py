"""Apply approved amount corrections without changing source transactions."""
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from apps.transactions.detail import MAX_MONEY, cent_value
from apps.transactions.models import (
    CanonicalTransaction,
    TransactionTax,
)
from apps.corrections.models import TransactionAdjustment


def _approved_value(adjustment: TransactionAdjustment, allowed_fields: set[str]) -> tuple[str, Decimal]:
    values = adjustment.proposed_values
    if not isinstance(values, dict) or len(values) != 1:
        raise ValueError(f"Approved adjustment {adjustment.id} has invalid stored values")
    field, raw_value = next(iter(values.items()))
    if field not in allowed_fields:
        raise ValueError(f"Approved adjustment {adjustment.id} has an unsupported stored value")
    try:
        amount = cent_value(raw_value, field)
    except ValueError as exc:
        raise ValueError(f"Approved adjustment {adjustment.id} has an invalid {field}") from exc
    if abs(amount) > MAX_MONEY:
        raise ValueError(f"Approved adjustment {adjustment.id} exceeds supported monetary precision")
    return field, amount


async def effective_amounts(db: AsyncSession, transactions: list[CanonicalTransaction]) -> dict[str, Decimal]:
    amounts = {transaction.id: transaction.signed_amount for transaction in transactions}
    ids = list(amounts)
    for offset in range(0, len(ids), 500):
        query = select(TransactionAdjustment).where(
            TransactionAdjustment.transaction_id.in_(ids[offset:offset + 500]),
            TransactionAdjustment.status == "Approved", TransactionAdjustment.adjustment_type == "Amount",
        ).order_by(func.coalesce(TransactionAdjustment.reviewed_at, TransactionAdjustment.created_at),
                   TransactionAdjustment.id)
        result = await db.stream(query.execution_options(yield_per=500))
        try:
            async for adjustment in result.scalars():
                field, value = _approved_value(adjustment, {"amount_delta", "corrected_amount"})
                if field == "amount_delta":
                    amounts[adjustment.transaction_id] += value
                else:
                    amounts[adjustment.transaction_id] = value
        finally:
            await result.close()
    return amounts


async def effective_taxes(db: AsyncSession, transactions: list[CanonicalTransaction]) -> dict[str, Decimal]:
    """Return recorded transaction tax plus all approved tax deltas."""
    taxes = {transaction.id: Decimal("0") for transaction in transactions}
    ids = list(taxes)
    for offset in range(0, len(ids), 500):
        batch_ids = ids[offset:offset + 500]
        recorded = await db.execute(
            select(TransactionTax.transaction_id, func.sum(TransactionTax.tax_amount))
            .where(TransactionTax.transaction_id.in_(batch_ids))
            .group_by(TransactionTax.transaction_id)
        )
        taxes.update(recorded.all())
        adjustments = await db.stream(select(TransactionAdjustment).where(
            TransactionAdjustment.transaction_id.in_(batch_ids),
            TransactionAdjustment.status == "Approved",
            TransactionAdjustment.adjustment_type == "Tax",
        ).order_by(func.coalesce(TransactionAdjustment.reviewed_at, TransactionAdjustment.created_at),
                   TransactionAdjustment.id).execution_options(yield_per=500))
        try:
            async for adjustment in adjustments.scalars():
                _field, value = _approved_value(adjustment, {"tax_delta"})
                taxes[adjustment.transaction_id] += value
        finally:
            await adjustments.close()
    return taxes


async def sum_effective_amounts(
    db: AsyncSession, transaction_query: Select[tuple[CanonicalTransaction]],
) -> Decimal:
    """Sum corrected transaction amounts in bounded, deterministic primary-key pages."""
    total = Decimal("0")
    last_id: str | None = None
    while True:
        query = transaction_query
        if last_id is not None:
            query = query.where(CanonicalTransaction.id > last_id)
        rows = (await db.execute(query.order_by(CanonicalTransaction.id).limit(500))).scalars().all()
        if not rows:
            return total
        last_id = rows[-1].id
        amounts = await effective_amounts(db, rows)
        total += sum(amounts.values(), Decimal("0"))
