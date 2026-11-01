"""Operational reporting for immutable source deliveries."""

from typing import Annotated

from database import get_db
from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from apps.ingest.models import SourceRecord
from apps.security.authentication import require_role

router = APIRouter()


@router.get("/api/ingest/deliveries")
async def delivery_history(
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("it", "finance")),
):
    """Operational history grouped by immutable source delivery ID."""
    recent = (
        select(
            SourceRecord.source_system.label("source_system"),
            SourceRecord.delivery_id.label("delivery_id"),
            func.max(SourceRecord.received_at).label("received_at"),
        )
        .group_by(SourceRecord.source_system, SourceRecord.delivery_id)
        .order_by(
            func.max(SourceRecord.received_at).desc(),
            SourceRecord.source_system,
            SourceRecord.delivery_id,
        )
        .limit(limit)
        .subquery()
    )
    delivery_keys = (
        await db.execute(select(recent.c.source_system, recent.c.delivery_id, recent.c.received_at))
    ).all()
    if not delivery_keys:
        return []

    keys = [(source_system, delivery_id) for source_system, delivery_id, _ in delivery_keys]
    counts = (
        await db.execute(
            select(
                SourceRecord.source_system,
                SourceRecord.delivery_id,
                func.count().label("records"),
                func.sum(case((SourceRecord.status == "Processed", 1), else_=0)).label("processed"),
                func.sum(case((SourceRecord.status == "Quarantined", 1), else_=0)).label(
                    "quarantined"
                ),
            )
            .where(tuple_(SourceRecord.source_system, SourceRecord.delivery_id).in_(keys))
            .group_by(SourceRecord.source_system, SourceRecord.delivery_id)
        )
    ).all()

    ranked_errors = (
        select(
            SourceRecord.source_system.label("source_system"),
            SourceRecord.delivery_id.label("delivery_id"),
            SourceRecord.quarantine_reason.label("last_error"),
            func.row_number()
            .over(
                partition_by=(SourceRecord.source_system, SourceRecord.delivery_id),
                order_by=(SourceRecord.received_at.desc(), SourceRecord.id.desc()),
            )
            .label("row_number"),
        )
        .where(
            tuple_(SourceRecord.source_system, SourceRecord.delivery_id).in_(keys),
            SourceRecord.status == "Quarantined",
        )
        .subquery()
    )
    errors = (
        await db.execute(
            select(
                ranked_errors.c.source_system,
                ranked_errors.c.delivery_id,
                ranked_errors.c.last_error,
            ).where(ranked_errors.c.row_number == 1)
        )
    ).all()
    count_by_key = {
        (source, delivery): (records, processed, quarantined)
        for source, delivery, records, processed, quarantined in counts
    }
    error_by_key = {(source, delivery): error for source, delivery, error in errors}
    result = []
    for source, delivery, received_at in delivery_keys:
        records, processed, quarantined = count_by_key[(source, delivery)]
        result.append(
            {
                "source_system": source,
                "delivery_id": delivery,
                "received_at": received_at,
                "records": records,
                "processed": processed,
                "quarantined": quarantined,
                "last_error": error_by_key.get((source, delivery)),
            }
        )
    return result
