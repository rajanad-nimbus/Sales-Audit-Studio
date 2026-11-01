"""Read-only case endpoints for immutable transaction source lineage."""

from datetime import UTC, datetime
from typing import Annotated

from database import get_db
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from apps.audit.models import AuditEvent
from apps.cases.lineage import case_creation_has_transactions, case_transaction_ids_table
from apps.cases.models import Case
from apps.ingest.models import SourceRecord
from apps.security import scope
from apps.security.authentication import require_role
from apps.transactions.models import CanonicalTransaction

router = APIRouter()


@router.get("/api/cases/{case_id}/reconciliation")
async def case_reconciliation(
    case_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("analyst", "finance", "it", "auditor")),
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    cursor_timestamp: datetime | None = None,
    cursor_id: str | None = None,
):
    """Return the immutable source-record lineage used to create an exception case."""
    if (cursor_timestamp is None) != (cursor_id is None):
        raise HTTPException(
            status_code=422, detail="cursor_timestamp and cursor_id must be provided together"
        )
    if cursor_timestamp is not None and cursor_timestamp.tzinfo is None:
        raise HTTPException(status_code=422, detail="cursor_timestamp must include a timezone")
    case = (
        await db.execute(
            scope.restrict(select(Case).where(Case.id == case_id), user, Case.store_id)
        )
    ).scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    has_linked_transactions = (
        await db.execute(
            select(case_creation_has_transactions(db, AuditEvent))
            .where(AuditEvent.case_id == case_id, AuditEvent.event_type == "CASE_CREATED")
            .order_by(AuditEvent.created_at.asc(), AuditEvent.id)
            .limit(1)
        )
    ).scalar()
    contextual = not bool(has_linked_transactions)
    if has_linked_transactions:
        created_event = aliased(AuditEvent)
        transaction_ids = case_transaction_ids_table(db, created_event)
        query = (
            select(CanonicalTransaction, SourceRecord)
            .select_from(created_event)
            .join(transaction_ids, true())
            .join(CanonicalTransaction, CanonicalTransaction.id == transaction_ids.c.value)
            .outerjoin(SourceRecord, CanonicalTransaction.source_record_id == SourceRecord.id)
            .where(
                created_event.case_id == case_id,
                created_event.event_type == "CASE_CREATED",
                CanonicalTransaction.store_id == case.store_id,
            )
            .distinct()
        )
    else:
        # Legacy/demo cases cannot claim a causal link; return only relevant context.
        query = (
            select(CanonicalTransaction, SourceRecord)
            .outerjoin(SourceRecord, CanonicalTransaction.source_record_id == SourceRecord.id)
            .where(
                CanonicalTransaction.store_id == case.store_id,
                CanonicalTransaction.business_date == case.business_date,
            )
        )
    if cursor_timestamp is not None and cursor_id is not None:
        query = query.where(
            or_(
                CanonicalTransaction.event_timestamp > cursor_timestamp,
                and_(
                    CanonicalTransaction.event_timestamp == cursor_timestamp,
                    CanonicalTransaction.id > cursor_id,
                ),
            )
        )
    rows = (
        await db.execute(
            query.order_by(CanonicalTransaction.event_timestamp, CanonicalTransaction.id).limit(
                limit + 1
            )
        )
    ).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    records = [
        {
            "transaction_id": tx.id,
            "source_system": source.source_system if source else "Unknown",
            "source_record_id": source.source_record_id if source else None,
            "transaction_type": tx.transaction_type,
            "business_date": tx.business_date,
            "event_timestamp": tx.event_timestamp,
            "amount": tx.signed_amount,
            "currency": tx.currency,
            "payment_reference": tx.payment_reference,
            "settlement_reference": tx.settlement_reference,
            "settlement_date": tx.settlement_date,
            "reconciliation_status": tx.reconciliation_status,
            "source_lineage": tx.source_lineage,
        }
        for tx, source in rows
    ]
    next_cursor = None
    if has_more and rows:
        last_transaction = rows[-1][0]
        event_timestamp = last_transaction.event_timestamp
        if event_timestamp.tzinfo is None:
            event_timestamp = event_timestamp.replace(tzinfo=UTC)
        next_cursor = {"event_timestamp": event_timestamp, "transaction_id": last_transaction.id}
    return {
        "case_id": case_id,
        "linked": not contextual,
        "message": (
            "Records recorded by the reconciliation run that created this case."
            if not contextual
            else "No source links were recorded for this legacy case; showing same-store, same-date context only."
        ),
        "records": records,
        "sources": sorted({record["source_system"] for record in records}),
        "has_more": has_more,
        "next_cursor": next_cursor,
    }
