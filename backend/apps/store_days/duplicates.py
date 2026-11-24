"""The store-day agent resolves duplicate-sale findings that are exact copies of a stored sale."""

from datetime import UTC, datetime

from integrations.ontology import nimbus
from integrations.ontology.nimbus import audit
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.audit.models import AuditEvent
from apps.cases.models import Case
from apps.cases.models import Exception as DBException
from apps.ingest.common import LINEAGE
from apps.ingest.models import SourceRecord
from apps.ingest.reconciliation import duplicate_transaction_key
from apps.store_days.freshness import invalidate_totals
from apps.store_days.models import StoreDay
from apps.transactions.models import CanonicalTransaction, TransactionLine, TransactionTender

EXCLUDED = "Duplicate"
EXCLUDED_EVENT = "DUPLICATE_SALE_EXCLUDED"
DUPLICATE_TYPES = ("DUPLICATE_SALES", "SHOPIFY_DUPLICATE_ORDER")


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


async def _shopify_original_of(db: AsyncSession, copy: CanonicalTransaction):
    """The Shopify order stored first under the same order id; None unless it is the only one."""
    if not copy.payment_reference:
        return None
    rows = (
        (
            await db.execute(
                select(CanonicalTransaction)
                .join(SourceRecord, CanonicalTransaction.source_record_id == SourceRecord.id)
                .where(
                    CanonicalTransaction.source_lineage == LINEAGE["Shopify"],
                    CanonicalTransaction.transaction_type == "Order",
                    CanonicalTransaction.store_id == copy.store_id,
                    CanonicalTransaction.currency == copy.currency,
                    CanonicalTransaction.payment_reference == copy.payment_reference,
                    CanonicalTransaction.id != copy.id,
                    CanonicalTransaction.disposition != EXCLUDED,
                )
                .order_by(SourceRecord.received_at, CanonicalTransaction.id)
            )
        )
        .scalars()
        .all()
    )
    return rows[0] if len(rows) == 1 else None


async def _original_of(db: AsyncSession, copy: CanonicalTransaction):
    if copy.source_lineage == LINEAGE["Shopify"] and copy.transaction_type == "Order":
        return await _shopify_original_of(db, copy)
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


async def _unlinked_copy_ids(db: AsyncSession, store_id: str, business_date: str) -> list[str]:
    """Legacy cases recorded no transaction link, so find the copies on the store day itself.

    Of two sales that share a duplicate key, the later one is the copy.
    """
    day = (
        (
            await db.execute(
                select(CanonicalTransaction)
                .where(
                    CanonicalTransaction.store_id == store_id,
                    CanonicalTransaction.business_date == business_date,
                    CanonicalTransaction.disposition != EXCLUDED,
                )
                .order_by(CanonicalTransaction.event_timestamp, CanonicalTransaction.id)
            )
        )
        .scalars()
        .all()
    )
    copies = []
    for t in day:
        original = await _original_of(db, t)
        if original is not None and (day.index(original) < day.index(t)):
            copies.append(t.id)
    return copies


async def resolve_duplicate_cases(db: AsyncSession, store_id: str, business_date: str, actor: str) -> int:
    """Close each open duplicate-sale case whose flagged sale is an exact copy, and drop the copy.

    Anything that differs from the stored sale, or has no single original, stays for a person.
    Does not commit; the caller re-totals the day afterwards.
    """
    from apps.agents import agent_settings

    settings = await agent_settings.load(db)
    if not settings["act_duplicate_enabled"]:
        return 0
    # The ontology decides which failure classes the agent may resolve on its own.
    acting = [t for t in DUPLICATE_TYPES if nimbus.agent_level(t) == "act"]
    if not acting:
        return 0
    start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    remaining = settings["act_daily_cap"] - (
        await db.execute(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.event_type == EXCLUDED_EVENT, AuditEvent.created_at >= start)
        )
    ).scalar_one()
    cases = (
        (
            await db.execute(
                select(Case)
                .join(DBException, DBException.case_id == Case.id)
                .where(
                    Case.store_id == store_id,
                    Case.business_date == business_date,
                    Case.status != "Closed",
                    DBException.exception_type.in_(acting),
                )
                .with_for_update(of=Case)
            )
        )
        .scalars()
        .all()
    )
    resolved = 0
    for case in cases:
        if resolved >= remaining:
            break
        created = (
            await db.execute(
                select(AuditEvent).where(
                    AuditEvent.case_id == case.id, AuditEvent.event_type == "CASE_CREATED"
                )
            )
        ).scalars().first()
        ids = ((created.after_state or {}).get("transaction_ids") or []) if created else []
        if not ids:
            ids = await _unlinked_copy_ids(db, store_id, business_date)
        if not ids:
            continue
        copy = await db.get(CanonicalTransaction, ids[0])
        if copy is None or copy.disposition == EXCLUDED:
            continue
        original = await _original_of(db, copy)
        if original is None or await _fingerprint(db, copy) != await _fingerprint(db, original):
            continue
        before_disposition = copy.disposition
        copy.disposition = EXCLUDED
        case.status, case.investigation_status = "Closed", "Auto-Resolved"
        for exception in (
            await db.execute(select(DBException).where(DBException.case_id == case.id))
        ).scalars():
            exception.status = "Closed"
        audit(
            db,
            EXCLUDED_EVENT,
            case.id,
            "CanonicalTransaction",
            copy.id,
            f"Exact copy of transaction {original.id} excluded from totals; case closed",
            actor=actor,
            before={"disposition": before_disposition},
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


async def reopen_auto_resolved(db: AsyncSession, case: Case, actor: str, reason: str) -> None:
    """Undo the agent's resolution: restore the excluded sale, reopen the case, re-total the day."""
    if case.investigation_status != "Auto-Resolved":
        raise ValueError("Only a case the agent resolved can be reopened this way")
    event = (
        (
            await db.execute(
                select(AuditEvent)
                .where(AuditEvent.case_id == case.id, AuditEvent.event_type == EXCLUDED_EVENT)
                .order_by(AuditEvent.created_at.desc())
            )
        )
        .scalars()
        .first()
    )
    copy = await db.get(CanonicalTransaction, event.object_id) if event else None
    if copy is not None and copy.disposition == EXCLUDED:
        copy.disposition = (event.before_state or {}).get("disposition") or "Open"
    case.status, case.investigation_status = "Open", "Pending"
    for exception in (
        await db.execute(select(DBException).where(DBException.case_id == case.id))
    ).scalars():
        exception.status = "Open"
    audit(
        db,
        "CASE_REOPENED",
        case.id,
        "Case",
        case.id,
        f"Agent resolution reversed by {actor}: {reason}",
        actor=actor,
        after={"restored_transaction_id": copy.id if copy else None},
    )
    day = (
        await db.execute(
            select(StoreDay).where(
                StoreDay.store_id == case.store_id, StoreDay.business_date == case.business_date
            )
        )
    ).scalar_one_or_none()
    if day is not None:
        await invalidate_totals(db, day, actor)
