"""Store-day view of source imports and downstream export batches."""

from database import get_db
from fastapi import APIRouter, Depends
from sqlalchemy import case, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from apps.exports.models import ExportBatch, ExportDestination, ExportItem
from apps.ingest.models import SourceRecord
from apps.security import scope
from apps.security.authentication import require_role
from apps.store_days.service import get_day
from apps.transactions.models import CanonicalTransaction

router = APIRouter(prefix="/api/store-days", tags=["Store-day import and export log"])


@router.get("/{store_id}/{business_date}/io-log")
async def store_day_io_log(
    store_id: str,
    business_date: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("analyst", "finance", "it", "auditor")),
):
    """Show deliveries contributing transactions and export batches containing day records."""
    scope.require_store(user, store_id)
    day = await get_day(db, store_id, business_date)

    imports = (
        await db.execute(
            select(
                SourceRecord.source_system,
                SourceRecord.delivery_id,
                func.max(SourceRecord.received_at).label("received_at"),
                func.count(func.distinct(CanonicalTransaction.id)).label("store_day_records"),
            )
            .join(
                CanonicalTransaction,
                CanonicalTransaction.source_record_id == SourceRecord.id,
            )
            .where(
                CanonicalTransaction.store_id == store_id,
                CanonicalTransaction.business_date == business_date,
            )
            .group_by(SourceRecord.source_system, SourceRecord.delivery_id)
            .order_by(func.max(SourceRecord.received_at).desc(), SourceRecord.source_system)
        )
    ).all()
    import_keys = [(row.source_system, row.delivery_id) for row in imports]
    delivery_totals: dict[tuple[str, str], tuple[int, int, int]] = {}
    if import_keys:
        totals = (
            await db.execute(
                select(
                    SourceRecord.source_system,
                    SourceRecord.delivery_id,
                    func.count().label("records"),
                    func.sum(case((SourceRecord.status == "Processed", 1), else_=0)).label(
                        "processed"
                    ),
                    func.sum(case((SourceRecord.status == "Quarantined", 1), else_=0)).label(
                        "quarantined"
                    ),
                )
                .where(
                    tuple_(SourceRecord.source_system, SourceRecord.delivery_id).in_(import_keys)
                )
                .group_by(SourceRecord.source_system, SourceRecord.delivery_id)
            )
        ).all()
        delivery_totals = {
            (row.source_system, row.delivery_id): (
                row.records,
                row.processed or 0,
                row.quarantined or 0,
            )
            for row in totals
        }

    exports = (
        await db.execute(
            select(
                ExportBatch.id,
                ExportBatch.dataset,
                ExportBatch.status,
                ExportBatch.created_at,
                ExportBatch.sent_at,
                ExportBatch.acked_at,
                ExportBatch.record_count,
                ExportBatch.error,
                ExportBatch.response_ref,
                ExportBatch.retries,
                ExportDestination.name.label("destination"),
                func.count(ExportItem.id).label("store_day_records"),
            )
            .join(ExportItem, ExportItem.batch_id == ExportBatch.id)
            .join(ExportDestination, ExportDestination.id == ExportBatch.destination_id)
            .where(
                ExportItem.store_id == store_id,
                ExportItem.business_date == business_date,
            )
            .group_by(
                ExportBatch.id,
                ExportBatch.dataset,
                ExportBatch.status,
                ExportBatch.created_at,
                ExportBatch.sent_at,
                ExportBatch.acked_at,
                ExportBatch.record_count,
                ExportBatch.error,
                ExportBatch.response_ref,
                ExportBatch.retries,
                ExportDestination.name,
            )
            .order_by(ExportBatch.created_at.desc(), ExportBatch.id)
        )
    ).all()

    return {
        "store_day_id": day.id,
        "store_id": store_id,
        "business_date": business_date,
        "imports": [
            {
                "source_system": row.source_system,
                "delivery_id": row.delivery_id,
                "received_at": row.received_at,
                "store_day_records": row.store_day_records,
                "delivery_records": delivery_totals[(row.source_system, row.delivery_id)][0],
                "delivery_processed": delivery_totals[(row.source_system, row.delivery_id)][1],
                "delivery_quarantined": delivery_totals[(row.source_system, row.delivery_id)][2],
            }
            for row in imports
        ],
        "exports": [
            {
                "batch_id": row.id,
                "destination": row.destination,
                "dataset": row.dataset,
                "status": row.status,
                "created_at": row.created_at,
                "sent_at": row.sent_at,
                "acked_at": row.acked_at,
                "batch_records": row.record_count,
                "store_day_records": row.store_day_records,
                "error": row.error,
                "response_ref": row.response_ref,
                "retries": row.retries,
            }
            for row in exports
        ],
    }
