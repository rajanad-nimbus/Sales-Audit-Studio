"""Operational reporting for immutable source deliveries."""

from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated

from database import get_db
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import case, func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from apps.csv_export import csv_table
from apps.ingest.manifests import validate_business_date
from apps.ingest.models import SourceRecord
from apps.security.authentication import require_role

router = APIRouter()
QUARANTINE_PAGE_SIZE = 50
QUARANTINE_MAX_PAGE_SIZE = 200
QUARANTINE_CSV_CHUNK = 1000
QUARANTINE_CSV_MAX_ROWS = 50_000
DELIVERY_PAGE_SIZE = 50
DELIVERY_MAX_PAGE_SIZE = 200
DELIVERY_CSV_CHUNK = 1000
DELIVERY_CSV_MAX_ROWS = 50_000


def delivery_filters(
    source_system: str | None,
    query: str | None,
    start: str | None,
    end: str | None,
) -> list:
    filters = []
    if source_system:
        filters.append(SourceRecord.source_system == source_system)
    if query:
        filters.append(SourceRecord.delivery_id.contains(query, autoescape=True))
    for value, lower_bound in ((start, True), (end, False)):
        if not value:
            continue
        try:
            validate_business_date(value)
            business_date = date.fromisoformat(value)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        boundary = datetime.combine(business_date, time.min, tzinfo=UTC)
        if lower_bound:
            filters.append(SourceRecord.received_at >= boundary)
        else:
            filters.append(SourceRecord.received_at < boundary + timedelta(days=1))
    if start and end and start > end:
        raise HTTPException(status_code=422, detail="start must be on or before end")
    return filters


async def load_delivery_page(
    db: AsyncSession, filters: list, offset: int, limit: int
) -> tuple[list[dict], bool]:
    """Aggregate a page of delivery IDs and return its records and continuation flag."""
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
        .where(*filters)
        .limit(limit + 1)
        .offset(offset)
        .subquery()
    )
    delivery_keys = (
        await db.execute(select(recent.c.source_system, recent.c.delivery_id, recent.c.received_at))
    ).all()
    has_more = len(delivery_keys) > limit
    delivery_keys = delivery_keys[:limit]
    if not delivery_keys:
        return [], False

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
    return result, has_more


@router.get("/api/ingest/deliveries")
async def delivery_history(
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("it", "finance", "auditor")),
):
    """Retain the compact latest-deliveries view for integrations using the legacy endpoint."""
    rows, _ = await load_delivery_page(db, [], 0, limit)
    return rows


@router.get("/api/ingest/deliveries/history")
async def paged_delivery_history(
    source_system: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
    query: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    start: Annotated[str | None, Query(min_length=10, max_length=10)] = None,
    end: Annotated[str | None, Query(min_length=10, max_length=10)] = None,
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    page_size: Annotated[int, Query(ge=10, le=DELIVERY_MAX_PAGE_SIZE)] = DELIVERY_PAGE_SIZE,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("it", "finance", "auditor")),
):
    """Search and page through source delivery summaries."""
    items, has_more = await load_delivery_page(
        db, delivery_filters(source_system, query, start, end), (page - 1) * page_size, page_size
    )
    return {"items": items, "page": page, "page_size": page_size, "has_more": has_more}


@router.get("/api/ingest/deliveries/history.csv")
async def export_delivery_history(
    source_system: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
    query: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    start: Annotated[str | None, Query(min_length=10, max_length=10)] = None,
    end: Annotated[str | None, Query(min_length=10, max_length=10)] = None,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("it", "finance", "auditor")),
):
    """Export matching delivery summaries, capped to keep report work bounded."""
    filters = delivery_filters(source_system, query, start, end)
    exported = []
    offset, has_more = 0, True
    while has_more and len(exported) < DELIVERY_CSV_MAX_ROWS:
        rows, has_more = await load_delivery_page(db, filters, offset, DELIVERY_CSV_CHUNK)
        exported.extend(
            [
                row["source_system"],
                row["delivery_id"],
                row["received_at"],
                row["records"],
                row["processed"],
                row["quarantined"],
                row["last_error"] or "",
            ]
            for row in rows
        )
        offset += DELIVERY_CSV_CHUNK
        if not rows:
            break
    truncated = has_more and len(exported) >= DELIVERY_CSV_MAX_ROWS
    return Response(
        csv_table(
            [
                "Source",
                "Delivery ID",
                "Received at",
                "Records",
                "Processed",
                "Quarantined",
                "Latest error",
            ],
            exported[:DELIVERY_CSV_MAX_ROWS],
        ),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=source-deliveries_{start or 'start'}_{end or 'now'}.csv",
            "X-Delivery-History-Truncated": "true" if truncated else "false",
        },
    )


def quarantine_filters(
    source_system: str | None,
    delivery_id: str | None,
    query: str | None,
    start: str | None,
    end: str | None,
) -> list:
    """Build validated filters shared by the quarantine table and CSV export."""
    filters = [SourceRecord.status == "Quarantined"]
    if source_system:
        filters.append(SourceRecord.source_system == source_system)
    if delivery_id:
        filters.append(SourceRecord.delivery_id == delivery_id)
    if query:
        filters.append(
            or_(
                SourceRecord.source_record_id.contains(query, autoescape=True),
                SourceRecord.quarantine_reason.contains(query, autoescape=True),
            )
        )
    for value, lower_bound in ((start, True), (end, False)):
        if not value:
            continue
        try:
            validate_business_date(value)
            business_date = date.fromisoformat(value)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        boundary = datetime.combine(business_date, time.min, tzinfo=UTC)
        if lower_bound:
            filters.append(SourceRecord.received_at >= boundary)
        else:
            filters.append(SourceRecord.received_at < boundary + timedelta(days=1))
    if start and end and start > end:
        raise HTTPException(status_code=422, detail="start must be on or before end")
    return filters


def quarantine_view(row: SourceRecord) -> dict:
    return {
        "id": row.id,
        "source_system": row.source_system,
        "delivery_id": row.delivery_id,
        "source_record_id": row.source_record_id,
        "reason": row.quarantine_reason or "",
        "received_at": row.received_at,
    }


@router.get("/api/ingest/quarantine")
async def quarantine_records(
    source_system: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
    delivery_id: Annotated[str | None, Query(min_length=1, max_length=255)] = None,
    query: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    start: Annotated[str | None, Query(min_length=10, max_length=10)] = None,
    end: Annotated[str | None, Query(min_length=10, max_length=10)] = None,
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    page_size: Annotated[int, Query(ge=10, le=QUARANTINE_MAX_PAGE_SIZE)] = QUARANTINE_PAGE_SIZE,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("analyst", "it", "auditor")),
):
    """Page through all quarantined records with source, date, and text filters."""
    rows = list(
        (
            await db.execute(
                select(SourceRecord)
                .where(*quarantine_filters(source_system, delivery_id, query, start, end))
                .order_by(SourceRecord.received_at.desc(), SourceRecord.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size + 1)
            )
        )
        .scalars()
        .all()
    )
    has_more = len(rows) > page_size
    return {
        "items": [quarantine_view(row) for row in rows[:page_size]],
        "page": page,
        "page_size": page_size,
        "has_more": has_more,
    }


@router.get("/api/ingest/quarantine.csv")
async def export_quarantine_records(
    source_system: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
    delivery_id: Annotated[str | None, Query(min_length=1, max_length=255)] = None,
    query: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    start: Annotated[str | None, Query(min_length=10, max_length=10)] = None,
    end: Annotated[str | None, Query(min_length=10, max_length=10)] = None,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("analyst", "it", "auditor")),
):
    """Export matching quarantine metadata in bounded pages, without source payloads."""
    filters = quarantine_filters(source_system, delivery_id, query, start, end)
    exported: list[list] = []
    offset, has_more = 0, True
    while has_more and len(exported) < QUARANTINE_CSV_MAX_ROWS:
        rows = list(
            (
                await db.execute(
                    select(SourceRecord)
                    .where(*filters)
                    .order_by(SourceRecord.received_at.desc(), SourceRecord.id.desc())
                    .offset(offset)
                    .limit(QUARANTINE_CSV_CHUNK + 1)
                )
            )
            .scalars()
            .all()
        )
        has_more = len(rows) > QUARANTINE_CSV_CHUNK
        exported.extend(
            [
                row.source_system,
                row.delivery_id,
                row.source_record_id,
                row.received_at,
                row.quarantine_reason or "",
            ]
            for row in rows[:QUARANTINE_CSV_CHUNK]
        )
        offset += QUARANTINE_CSV_CHUNK
        if not rows:
            break
    truncated = has_more and len(exported) >= QUARANTINE_CSV_MAX_ROWS
    return Response(
        csv_table(
            ["Source", "Delivery ID", "Source record ID", "Received at", "Quarantine reason"],
            exported,
        ),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=quarantine_{start or 'start'}_{end or 'now'}.csv",
            "X-Quarantine-Truncated": "true" if truncated else "false",
        },
    )
