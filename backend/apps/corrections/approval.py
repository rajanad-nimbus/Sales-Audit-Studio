"""Transactional work used to approve a proposed transaction correction."""

import hashlib
import json
import uuid
from dataclasses import dataclass
from dataclasses import field
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.corrections.effects import effective_amounts
from apps.corrections.reconciliation import (
    invalidate_case_evidence,
    linked_cases,
    related_exception_ids,
    requeue_linked_exceptions,
)
from apps.ingest.common import LINEAGE, SIGN
from apps.ingest.models import SourceRecord
from apps.exports.models import ExportItem
from apps.security import scope
from apps.store_days.freshness import invalidate_totals, lock_store_day
from apps.transactions.detail import MAX_MONEY
from apps.transactions.models import CanonicalTransaction
from model_base import utcnow


@dataclass
class ApprovalContext:
    """Validated, locked state required to apply an approval atomically."""

    store_id: str
    business_date: str
    values: dict
    transaction: CanonicalTransaction | None = None
    day: object | None = None
    cases: list = field(default_factory=list)
    locked_transactions: list | None = None
    missing_transaction: tuple | None = None


async def prepare_approval(
    db: AsyncSession, adjustment, user: dict, missing_transaction: tuple | None = None
) -> ApprovalContext:
    """Validate scope, lock affected rows, and check dependent workflows."""
    if adjustment.adjustment_type == "MissingTransaction":
        amount, transaction_type, business_date, event_at, settlement_date, source = missing_transaction
        context = ApprovalContext(
            store_id=str(adjustment.proposed_values["store_id"]),
            business_date=business_date,
            values=adjustment.proposed_values,
            missing_transaction=(amount, transaction_type, event_at, settlement_date, source),
            cases=[],
        )
    else:
        transaction = await db.get(CanonicalTransaction, adjustment.transaction_id)
        if transaction is None:
            raise HTTPException(status_code=409, detail="Adjustment transaction no longer exists")
        context = ApprovalContext(
            store_id=transaction.store_id,
            business_date=transaction.business_date,
            values=adjustment.proposed_values,
            transaction=transaction,
            cases=[],
        )

    scope.require_store(user, context.store_id)
    context.day = await lock_store_day(db, context.store_id, context.business_date)
    if context.transaction is not None:
        context.cases = await linked_cases(db, context.transaction)
        await ensure_linked_workflows_are_idle(db, context.cases)
        if adjustment.adjustment_type == "Amount":
            context.locked_transactions = await lock_related_transactions(db, context.transaction)
            context.transaction = next(
                row for row in context.locked_transactions if row.id == adjustment.transaction_id
            )
            validate_effective_amount(context.transaction, context.values, await effective_amounts(db, [context.transaction]))
    return context


async def ensure_linked_workflows_are_idle(db: AsyncSession, cases: list) -> None:
    """Prevent changing evidence while a case workflow or export is in flight."""
    if any(case.status in {"Resolving", "Pending Validation"} for case in cases):
        raise HTTPException(
            status_code=409,
            detail="A linked case has an active workflow; resolve it before approving this correction",
        )
    if not cases:
        return
    pending = (
        await db.execute(
            select(ExportItem.id)
            .where(
                ExportItem.object_id.in_([case.id for case in cases]),
                ExportItem.dataset.in_(["resolutions", "adjustments"]),
                ExportItem.state == "Pending",
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if pending:
        raise HTTPException(
            status_code=409,
            detail="A linked case has a pending export; retry or cancel the batch before approving this correction",
        )


async def lock_related_transactions(db: AsyncSession, transaction: CanonicalTransaction) -> list:
    """Lock the complete linked exception set in stable order for amount checks."""
    transaction_ids = await related_exception_ids(db, transaction)
    return (
        await db.execute(
            select(CanonicalTransaction)
            .where(CanonicalTransaction.id.in_(transaction_ids))
            .order_by(CanonicalTransaction.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalars().all()


def validate_effective_amount(transaction: CanonicalTransaction, values: dict, amounts: dict) -> None:
    """Reject effective amounts that overflow or reverse transaction direction."""
    current = amounts[transaction.id]
    result = (
        Decimal(str(values["corrected_amount"]))
        if "corrected_amount" in values
        else current + Decimal(str(values["amount_delta"]))
    )
    if abs(result) > MAX_MONEY:
        raise HTTPException(status_code=422, detail="Adjusted amount exceeds supported monetary precision")
    expected_sign = SIGN.get(transaction.transaction_type)
    if expected_sign is None or result * expected_sign < 0:
        raise HTTPException(status_code=422, detail="Adjusted amount cannot reverse the transaction's financial sign")


async def apply_approval_effects(db: AsyncSession, adjustment, context: ApprovalContext, actor: str) -> None:
    """Apply reconciliation, freshness, and missing-transaction effects in this transaction."""
    await invalidate_case_evidence(db, context.cases, adjustment.id, actor)
    if adjustment.adjustment_type == "Amount":
        await requeue_linked_exceptions(
            db, context.transaction, context.locked_transactions, actor
        )
    if context.day:
        await invalidate_totals(db, context.day, actor)
    if context.missing_transaction:
        adjustment.transaction_id = await create_missing_transaction(db, adjustment, context)


async def create_missing_transaction(db: AsyncSession, adjustment, context: ApprovalContext) -> str:
    """Create immutable source lineage and its canonical transaction together."""
    amount, transaction_type, event_at, settlement_date, source = context.missing_transaction
    values = context.values
    source_id = str(uuid.uuid4())
    payload = json.dumps(
        {"adjustment_id": adjustment.id, "values": values, "rationale": adjustment.rationale},
        sort_keys=True,
        default=str,
    ).encode()
    now = utcnow()
    db.add(
        SourceRecord(
            id=source_id,
            source_system="ManualAdjustment",
            source_version="1.0",
            source_record_id=f"adjustment-{adjustment.id}",
            delivery_id=adjustment.id,
            payload=payload,
            payload_hash=hashlib.sha256(payload).hexdigest(),
            received_at=now,
            source_event_time=event_at,
            status="Processed",
            extracted_at=now,
        )
    )
    await db.flush()
    transaction_id = str(uuid.uuid4())
    db.add(
        CanonicalTransaction(
            id=transaction_id,
            business_date=context.business_date,
            transaction_date=event_at,
            event_timestamp=event_at,
            processing_timestamp=utcnow(),
            settlement_date=settlement_date,
            transaction_type=transaction_type,
            transaction_subtype=None,
            source_lineage=LINEAGE[source],
            currency=str(values.get("currency") or "USD"),
            signed_amount=-amount if transaction_type in {"Return", "Refund"} else amount,
            quantity=values.get("quantity"),
            store_id=context.store_id,
            register_id=values.get("register_id"),
            channel_id=str(values.get("channel_id") or "store"),
            tender_type=values.get("tender_type"),
            payment_reference=values.get("payment_reference"),
            settlement_reference=values.get("settlement_reference"),
            reconciliation_status="Unmatched",
            source_record_id=source_id,
            disposition="Open",
        )
    )
    await db.flush()
    return transaction_id
