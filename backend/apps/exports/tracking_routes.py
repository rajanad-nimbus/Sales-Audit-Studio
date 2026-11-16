"""HTTP endpoint for export delivery tracking."""

from typing import Annotated

from database import get_db
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.audit.models import AuditEvent
from apps.exports.catalog import DATASETS
from apps.exports.models import ExportBatch, ExportDestination
from apps.csv_export import csv_table
from apps.exports.tracking import (
    TRACK_STATES,
    audited_totals_tracking_page,
    audited_transactions_tracking_page,
    tracking_page,
    transaction_corrections_tracking_page,
)
from apps.security.authentication import require_role

router = APIRouter()
TRACKING_CSV_CHUNK = 1000
TRACKING_CSV_MAX_ROWS = 50_000


async def get_tracking_page(db, destination_id, dataset, state, q, page, page_size):
    if dataset in {"audited_totals", "audited_transactions", "transaction_corrections"}:
        page_builder = (
            audited_totals_tracking_page
            if dataset == "audited_totals"
            else audited_transactions_tracking_page
            if dataset == "audited_transactions"
            else transaction_corrections_tracking_page
        )
        return await page_builder(db, destination_id, state, q, page, page_size)
    return await tracking_page(db, destination_id, dataset, state, q, page, page_size)


@router.get("/tracking")
async def tracking(
    destination_id: str,
    dataset: str,
    state: Annotated[str, Query(max_length=20)] = "",
    q: Annotated[str, Query(max_length=120)] = "",
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("finance", "it", "auditor")),
):
    if dataset not in DATASETS:
        raise HTTPException(status_code=400, detail="Unknown dataset")
    if state and state not in TRACK_STATES:
        raise HTTPException(status_code=400, detail=f"state must be one of {TRACK_STATES}")
    if not (
        await db.execute(select(ExportDestination.id).where(ExportDestination.id == destination_id))
    ).scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Destination not found")
    if dataset == "audit":
        # The audit trail is exported as a sequence, so coverage is a position rather than a record list.
        last = (
            await db.execute(
                select(func.max(ExportBatch.range_to)).where(
                    ExportBatch.destination_id == destination_id,
                    ExportBatch.dataset == "audit",
                    ExportBatch.status.in_(["Sent", "Acknowledged"]),
                )
            )
        ).scalar() or 0
        head = (await db.execute(select(func.max(AuditEvent.seq)))).scalar() or 0
        waiting = (
            await db.execute(
                select(func.count()).select_from(AuditEvent).where(AuditEvent.seq > last)
            )
        ).scalar() or 0
        return {
            "kind": "stream",
            "summary": {"last_exported_seq": last, "head_seq": head, "waiting_events": waiting},
            "items": [],
            "total": 0,
            "page": 1,
            "pages": 1,
        }
    if dataset in {
        "audited_totals",
        "audited_transactions",
        "transaction_corrections",
    }:
        summary, items, total = await get_tracking_page(
            db, destination_id, dataset, state, q, page, page_size
        )
    else:
        summary, items, total = await tracking_page(
            db, destination_id, dataset, state, q, page, page_size
        )
    return {
        "kind": "records",
        "summary": summary,
        "items": items,
        "total": total,
        "page": page,
        "pages": max(1, -(-total // page_size)),
    }


@router.get("/tracking.csv")
async def export_tracking(
    destination_id: str,
    dataset: str,
    state: Annotated[str, Query(max_length=20)] = "",
    q: Annotated[str, Query(max_length=120)] = "",
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("finance", "it", "auditor")),
):
    """Export every filtered delivery-tracking row, with a bounded row limit."""
    if dataset not in DATASETS:
        raise HTTPException(status_code=400, detail="Unknown dataset")
    if state and state not in TRACK_STATES:
        raise HTTPException(status_code=400, detail=f"state must be one of {TRACK_STATES}")
    if not (
        await db.execute(select(ExportDestination.id).where(ExportDestination.id == destination_id))
    ).scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Destination not found")

    if dataset == "audit":
        last = (
            await db.execute(
                select(func.max(ExportBatch.range_to)).where(
                    ExportBatch.destination_id == destination_id,
                    ExportBatch.dataset == "audit",
                    ExportBatch.status.in_(["Sent", "Acknowledged"]),
                )
            )
        ).scalar() or 0
        head = (await db.execute(select(func.max(AuditEvent.seq)))).scalar() or 0
        waiting = (
            await db.execute(
                select(func.count()).select_from(AuditEvent).where(AuditEvent.seq > last)
            )
        ).scalar() or 0
        data = csv_table(
            ["Stream", "Last exported sequence", "Current head sequence", "Waiting events"],
            [["Audit trail", last, head, waiting]],
        )
        return Response(
            data,
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=audit-stream-tracking.csv"},
        )

    rows: list[list] = []
    page, total = 1, 0
    while len(rows) < TRACKING_CSV_MAX_ROWS:
        _, items, total = await get_tracking_page(
            db, destination_id, dataset, state, q, page, TRACKING_CSV_CHUNK
        )
        rows.extend(
            [
                item["record_ref"],
                item["object_id"],
                item["store_id"],
                item["business_date"],
                item["state"],
                item["case_status"],
                item["batch_id"] or "",
                item["batch_status"] or "",
                item["delivered_at"] or "",
                "Yes" if item["backpost"] else "No",
                item["backpost_reason"] or "",
                item["reason"] or "",
            ]
            for item in items
        )
        if not items or page * TRACKING_CSV_CHUNK >= total:
            break
        page += 1
    truncated = total > TRACKING_CSV_MAX_ROWS
    data = csv_table(
        [
            "Record",
            "Object ID",
            "Store",
            "Business date",
            "Export state",
            "Business status",
            "Batch ID",
            "Batch status",
            "Delivered at",
            "Back-post",
            "Back-post reason",
            "Detail",
        ],
        rows[:TRACKING_CSV_MAX_ROWS],
    )
    return Response(
        data,
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=export-tracking-{dataset}.csv",
            "X-Tracking-Truncated": "true" if truncated else "false",
        },
    )
