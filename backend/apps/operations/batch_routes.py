"""HTTP routes and request contracts for daily batch operations."""

import json
from datetime import UTC, datetime, timedelta
from typing import Annotated

from database import get_db
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from model_base import utcnow
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.ingest.common import VALID_TYPES
from apps.ingest.contracts import json_payload_size
from apps.ingest.limits import MAX_DELIVERY_BYTES, MAX_DELIVERY_RECORDS
from apps.ingest.manifests import validate_business_date
from apps.operations.batch import run_daily_batch
from apps.operations.models import BatchRun
from apps.csv_export import csv_table
from apps.security.authentication import require_role
from apps.transactions.models import CanonicalTransaction

router = APIRouter(prefix="/api/batch")
STALE_AFTER_HOURS = 26
RUN_HISTORY_PAGE_SIZE = 50
RUN_HISTORY_MAX_PAGE_SIZE = 200
RUN_HISTORY_CSV_CHUNK = 1000
RUN_HISTORY_CSV_MAX_ROWS = 50_000


def view(run: BatchRun) -> dict:
    return {
        "id": run.id,
        "business_date": run.business_date,
        "status": run.status,
        "trigger": run.trigger,
        "started_at": run.started_at,
        "heartbeat_at": run.heartbeat_at,
        "finished_at": run.finished_at,
        "transactions_loaded": run.transactions_loaded,
        "matched_pairs": run.matched_pairs,
        "cases_created": run.cases_created,
        "store_day_open_status": run.store_day_open_status,
        "store_days_opened": run.store_days_opened,
        "store_days_already_open": run.store_days_already_open,
        "store_day_open_error": run.store_day_open_error,
        "source_counts": run.source_counts,
        "error": run.error,
    }


def status_view(run: BatchRun | None) -> dict | None:
    """Expose only the run metrics required by the all-role freshness banner."""
    if run is None:
        return None
    return {
        "business_date": run.business_date,
        "status": run.status,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "transactions_loaded": run.transactions_loaded,
        "matched_pairs": run.matched_pairs,
        "cases_created": run.cases_created,
    }


class RunRequest(BaseModel):
    business_date: str | None = None
    rerun: bool = False
    feeds: dict[str, list[dict]] | None = Field(default=None, max_length=len(VALID_TYPES))

    @model_validator(mode="after")
    def validate_feed_payload(self):
        if self.feeds is None:
            return self
        unsupported = set(self.feeds) - set(VALID_TYPES)
        if unsupported:
            raise ValueError(f"Unsupported feed sources: {', '.join(sorted(unsupported))}")
        record_count = sum(len(records) for records in self.feeds.values())
        if record_count > MAX_DELIVERY_RECORDS:
            raise ValueError(f"Batch exceeds the {MAX_DELIVERY_RECORDS:,}-record delivery limit")
        size = json_payload_size(self.feeds, MAX_DELIVERY_BYTES)
        if size > MAX_DELIVERY_BYTES:
            raise ValueError(f"Batch payload exceeds {MAX_DELIVERY_BYTES} bytes")
        return self


@router.post("/run")
async def run_batch(
    req: RunRequest,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("it")),
    request: Request = None,
):
    business_date = req.business_date
    if business_date is None:
        earliest = (await db.execute(select(func.min(CanonicalTransaction.business_date)))).scalar()
        business_date = (
            (datetime.fromisoformat(earliest) - timedelta(days=1)).strftime("%Y-%m-%d")
            if earliest
            else (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d")
        )
    run = await run_daily_batch(
        db, business_date, req.feeds, trigger="api", rerun=req.rerun, request=request
    )
    return view(run)


@router.get("/runs")
async def runs(
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("finance", "it")),
):
    rows = (
        (await db.execute(select(BatchRun).order_by(BatchRun.started_at.desc()).limit(limit)))
        .scalars()
        .all()
    )
    return [view(run) for run in rows]


def run_history_filters(start: str | None, end: str | None, status: str | None) -> list:
    """Validate optional history filters and return the matching predicates."""
    filters = []
    for value, column, operator in (
        (start, BatchRun.business_date, "ge"),
        (end, BatchRun.business_date, "le"),
    ):
        if value:
            try:
                validate_business_date(value)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            filters.append(column >= value if operator == "ge" else column <= value)
    if start and end and start > end:
        raise HTTPException(status_code=422, detail="start must be on or before end")
    if status:
        filters.append(BatchRun.status == status)
    return filters


def run_history_row(run: BatchRun) -> list:
    """Export operational outcomes without exposing raw exception text."""
    return [
        run.business_date,
        run.status,
        run.trigger,
        run.started_at,
        run.finished_at,
        run.transactions_loaded,
        run.matched_pairs,
        run.cases_created,
        run.store_day_open_status,
        run.store_days_opened,
        run.store_days_already_open,
    ]


RUN_HISTORY_COLUMNS = [
    "Business date",
    "Status",
    "Trigger",
    "Started at",
    "Finished at",
    "Transactions loaded",
    "Matched pairs",
    "Cases created",
    "Store-day opening status",
    "Store days opened",
    "Store days already open",
]


@router.get("/runs/history")
async def run_history(
    start: Annotated[str | None, Query(min_length=10, max_length=10)] = None,
    end: Annotated[str | None, Query(min_length=10, max_length=10)] = None,
    status: Annotated[str | None, Query(pattern="^(Running|Succeeded|Failed)$")] = None,
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    page_size: Annotated[int, Query(ge=10, le=RUN_HISTORY_MAX_PAGE_SIZE)] = RUN_HISTORY_PAGE_SIZE,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("finance", "it", "auditor")),
):
    """Page through batch history without counting the full history table."""
    query = (
        select(BatchRun)
        .where(*run_history_filters(start, end, status))
        .order_by(BatchRun.started_at.desc(), BatchRun.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size + 1)
    )
    found = list((await db.execute(query)).scalars())
    has_more = len(found) > page_size
    items = [view(run) for run in found[:page_size]]
    return {"items": items, "page": page, "page_size": page_size, "has_more": has_more}


@router.get("/runs/history.csv")
async def export_run_history(
    start: Annotated[str | None, Query(min_length=10, max_length=10)] = None,
    end: Annotated[str | None, Query(min_length=10, max_length=10)] = None,
    status: Annotated[str | None, Query(pattern="^(Running|Succeeded|Failed)$")] = None,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("finance", "it", "auditor")),
):
    """Export matching batch outcomes in bounded database chunks."""
    filters = run_history_filters(start, end, status)
    rows: list[list] = []
    offset, has_more = 0, True
    while has_more and len(rows) < RUN_HISTORY_CSV_MAX_ROWS:
        query = (
            select(BatchRun)
            .where(*filters)
            .order_by(BatchRun.started_at.desc(), BatchRun.id.desc())
            .offset(offset)
            .limit(RUN_HISTORY_CSV_CHUNK + 1)
        )
        found = list((await db.execute(query)).scalars())
        has_more = len(found) > RUN_HISTORY_CSV_CHUNK
        rows.extend(run_history_row(run) for run in found[:RUN_HISTORY_CSV_CHUNK])
        offset += RUN_HISTORY_CSV_CHUNK
        if not found:
            break
    truncated = has_more and len(rows) >= RUN_HISTORY_CSV_MAX_ROWS
    return Response(
        csv_table(RUN_HISTORY_COLUMNS, rows[:RUN_HISTORY_CSV_MAX_ROWS]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=batch-runs_{start or 'start'}_{end or 'now'}.csv",
            "X-Batch-History-Truncated": "true" if truncated else "false",
        },
    )


@router.get("/runs/{run_id}")
async def batch_run_detail(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("finance", "it", "auditor")),
):
    """Read one batch process by its stable ID for direct operational handoffs."""
    run = (
        await db.execute(select(BatchRun).where(BatchRun.id == run_id))
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Batch run not found")
    return view(run)


@router.get("/runs/{run_id}/export.csv")
async def export_batch_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("finance", "it", "auditor")),
):
    """Download one complete, sanitized batch outcome for an operational handoff."""
    run = (
        await db.execute(select(BatchRun).where(BatchRun.id == run_id))
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Batch run not found")
    columns = [
        "Run ID",
        "Business date",
        "Status",
        "Trigger",
        "Started at",
        "Last progress at",
        "Finished at",
        "Transactions loaded",
        "Matched pairs",
        "Cases created",
        "Source counts",
        "Run error",
        "Store-day opening status",
        "Store days opened",
        "Store days already open",
        "Store-day opening error",
    ]
    row = [
        run.id,
        run.business_date,
        run.status,
        run.trigger,
        run.started_at,
        run.heartbeat_at,
        run.finished_at,
        run.transactions_loaded,
        run.matched_pairs,
        run.cases_created,
        json.dumps(run.source_counts or {}, sort_keys=True, separators=(",", ":")),
        run.error,
        run.store_day_open_status,
        run.store_days_opened,
        run.store_days_already_open,
        run.store_day_open_error,
    ]
    return Response(
        csv_table(columns, [row]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="batch-run_{run.id}.csv"'},
    )


@router.get("/status")
async def status(db: AsyncSession = Depends(get_db)):
    latest_success = (
        (
            await db.execute(
                select(BatchRun)
                .where(BatchRun.status == "Succeeded")
                .order_by(BatchRun.finished_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    latest_run = (
        (await db.execute(select(BatchRun).order_by(BatchRun.started_at.desc()).limit(1)))
        .scalars()
        .first()
    )
    data_as_of = (await db.execute(select(func.max(CanonicalTransaction.business_date)))).scalar()
    if latest_success is None:
        return {
            "freshness": "never",
            "data_as_of": data_as_of,
            "last_success": None,
            "last_run": status_view(latest_run),
            "last_failed": bool(latest_run and latest_run.status == "Failed"),
        }

    finished_at = latest_success.finished_at
    if finished_at.tzinfo is None:
        finished_at = finished_at.replace(tzinfo=UTC)
    hours_since_success = (utcnow() - finished_at).total_seconds() / 3600
    return {
        "freshness": "stale" if hours_since_success > STALE_AFTER_HOURS else "fresh",
        "hours_since_success": round(hours_since_success, 1),
        "data_as_of": latest_success.business_date,
        "last_success": status_view(latest_success),
        "last_run": status_view(latest_run),
        "last_failed": bool(latest_run and latest_run.status == "Failed"),
    }
