"""Store-day view of source imports and downstream export batches."""

import json
from datetime import UTC, date, datetime, time, timedelta

from database import get_db
from fastapi import APIRouter, Depends
from sqlalchemy import and_, case, func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from apps.exports.models import ExportBatch, ExportDestination, ExportItem
from apps.ingest.models import SourceRecord
from apps.ingest.shopify.day_totals import RECORD_PREFIX as SHOPIFY_CONTROL_PREFIX
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
    """Show source controls, deliveries contributing transactions, and downstream exports."""
    scope.require_store(user, store_id)
    day = await get_day(db, store_id, business_date)

    control_day = date.fromisoformat(business_date)
    control_start = datetime.combine(control_day, time.min, tzinfo=UTC)
    control_filters = [
        or_(
            SourceRecord.source_system.in_(("POSControl", "OMSControl")),
            and_(
                SourceRecord.source_system == "Shopify",
                SourceRecord.source_record_id.like(f"{SHOPIFY_CONTROL_PREFIX}-%"),
            ),
        ),
        SourceRecord.status.in_(("Archived", "Processed", "Superseded")),
        SourceRecord.source_event_time >= control_start,
    ]
    if control_day != date.max:
        control_filters.append(SourceRecord.source_event_time < control_start + timedelta(days=1))
    control_records = (
        await db.execute(
            select(
                SourceRecord.source_system,
                SourceRecord.source_record_id,
                SourceRecord.delivery_id,
                SourceRecord.status,
                SourceRecord.received_at,
                SourceRecord.payload,
            )
            .where(*control_filters)
            .order_by(SourceRecord.received_at.desc(), SourceRecord.id)
        )
    ).all()
    controls = []
    control_sources = {"POSControl": "POS", "OMSControl": "OMS", "Shopify": "Shopify"}
    for (
        source_system,
        source_record_id,
        delivery_id,
        status,
        received_at,
        raw_payload,
    ) in control_records:
        try:
            payload = json.loads(raw_payload)
        except (TypeError, ValueError):
            continue
        if (
            payload.get("store_id") != store_id
            or str(payload.get("business_date", ""))[:10] != business_date
        ):
            continue
        if source_system == "Shopify" and payload.get("record_type") != "DayTotal":
            continue
        controls.append(
            {
                "source_system": control_sources[source_system],
                "source_record_id": source_record_id,
                "delivery_id": delivery_id,
                "status": status,
                "received_at": received_at,
                "record_count": payload.get("order_count", payload.get("declared_count")),
                "amount": payload.get("order_total", payload.get("declared_total")),
                "currency": payload.get("currency", "USD"),
                "refund_count": payload.get("refund_count"),
                "refund_amount": payload.get("refund_total"),
            }
        )

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
        "controls": controls,
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
