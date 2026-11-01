"""HTTP endpoint for export delivery tracking."""

from typing import Annotated

from database import get_db
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.audit.models import AuditEvent
from apps.exports.catalog import DATASETS
from apps.exports.models import ExportBatch, ExportDestination
from apps.exports.tracking import TRACK_STATES, tracking_page
from apps.security.authentication import require_role

router = APIRouter()


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
