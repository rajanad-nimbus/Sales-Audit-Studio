"""The store-day agent resolves duplicate-sale findings that are exact copies of a stored sale."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.audit.models import AuditEvent
from apps.cases.models import Case
from apps.cases.models import Exception as DBException
from apps.ingest.reconciliation import duplicate_transaction_key
from apps.store_days.freshness import invalidate_totals
from apps.store_days.models import StoreDay
from apps.transactions.models import CanonicalTransaction, TransactionLine, TransactionTender
from integrations.ontology.nimbus import audit

EXCLUDED = "Duplicate"


async def _fingerprint(db: AsyncSession, t: CanonicalTransaction) -> tuple:
    """What makes two sales the same sale. Payment references are left out on purpose: a copy that
    carries a different reference is still the same register and transaction number, and the
    references are recorded in the audit event instead."""
    lines = (
        await db.execute(select(TransactionLine).where(TransactionLine.transaction_id == t.id))
    ).scalars()
    tenders = (
        await db.execute(select(TransactionTender).where(TransactionTender.transaction_id == t.id))
    ).scalars()
    return (
        t.transaction_type,
        t.currency,
        t.signed_amount,
        t.tender_type,
        t.cashier_id,
        sorted((x.item_code, x.quantity, x.gross_amount, x.tax_amount) for x in lines),
        sorted((x.tender_type, x.client_tender_code, x.amount) for x in tenders),
    )


async def _original_of(db: AsyncSession, copy: CanonicalTransaction):
    key = duplicate_transaction_key(copy)
    if key is None:
        return None
    candidates = (
        (
            await db.execute(
                select(CanonicalTransaction).where(
                    CanonicalTransaction.store_id == copy.store_id,
                    CanonicalTransaction.business_date == copy.business_date,
                    CanonicalTransaction.transaction_number == copy.transaction_number,
                    CanonicalTransaction.id != copy.id,
                    CanonicalTransaction.disposition != EXCLUDED,
                )
            )
        )
        .scalars()
        .all()
    )
    matches = [c for c in candidates if duplicate_transaction_key(c) == key]
    return matches[0] if len(matches) == 1 else None


async def resolve_duplicate_cases(db: AsyncSession, store_id: str, business_date: str, actor: str) -> int:
    """Close each open duplicate-sale case whose flagged sale is an exact copy, and drop the copy.

    Anything that differs from the stored sale, or has no single original, stays for a person.
    Does not commit; the caller re-totals the day afterwards.
    """
    cases = (
        (
            await db.execute(
                select(Case)
                .join(DBException, DBException.case_id == Case.id)
                .where(
                    Case.store_id == store_id,
                    Case.business_date == business_date,
                    Case.status != "Closed",
                    DBException.exception_type == "DUPLICATE_SALES",
                )
                .with_for_update(of=Case)
            )
        )
        .scalars()
        .all()
    )
    resolved = 0
    for case in cases:
        created = (
            await db.execute(
                select(AuditEvent).where(
                    AuditEvent.case_id == case.id, AuditEvent.event_type == "CASE_CREATED"
                )
            )
        ).scalars().first()
        ids = ((created.after_state or {}).get("transaction_ids") or []) if created else []
        if not ids:
            continue
        copy = await db.get(CanonicalTransaction, ids[0])
        if copy is None or copy.disposition == EXCLUDED:
            continue
        original = await _original_of(db, copy)
        if original is None or await _fingerprint(db, copy) != await _fingerprint(db, original):
            continue
        copy.disposition = EXCLUDED
        case.status, case.investigation_status = "Closed", "Auto-Resolved"
        for exception in (
            await db.execute(select(DBException).where(DBException.case_id == case.id))
        ).scalars():
            exception.status = "Closed"
        audit(
            db,
            "DUPLICATE_SALE_EXCLUDED",
            case.id,
            "CanonicalTransaction",
            copy.id,
            f"Exact copy of transaction {original.id} excluded from totals; case closed",
            actor=actor,
            after={
                "original_transaction_id": original.id,
                "copy_payment_reference": copy.payment_reference,
                "original_payment_reference": original.payment_reference,
            },
        )
        resolved += 1
    if resolved:
        day = (
            await db.execute(
                select(StoreDay).where(
                    StoreDay.store_id == store_id, StoreDay.business_date == business_date
                )
            )
        ).scalar_one_or_none()
        if day is not None:
            await invalidate_totals(db, day, actor)
    return resolved
