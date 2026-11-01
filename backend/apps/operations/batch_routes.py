"""HTTP routes and request contracts for daily batch operations."""

from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.ingest.common import VALID_TYPES
from apps.ingest.contracts import json_payload_size
from apps.ingest.limits import MAX_DELIVERY_BYTES, MAX_DELIVERY_RECORDS
from apps.operations.batch import run_daily_batch
from apps.operations.models import BatchRun
from apps.security.authentication import require_role
from apps.transactions.models import CanonicalTransaction
from database import get_db
from model_base import utcnow

router = APIRouter(prefix="/api/batch")
STALE_AFTER_HOURS = 26


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
    req: RunRequest, db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("it"))
):
    business_date = req.business_date
    if business_date is None:
        earliest = (
            await db.execute(select(func.min(CanonicalTransaction.business_date)))
        ).scalar()
        business_date = (
            (datetime.fromisoformat(earliest) - timedelta(days=1)).strftime("%Y-%m-%d")
            if earliest
            else (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        )
    run = await run_daily_batch(db, business_date, req.feeds, trigger="api", rerun=req.rerun)
    return view(run)


@router.get("/runs")
async def runs(
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("finance", "it")),
):
    rows = (
        await db.execute(select(BatchRun).order_by(BatchRun.started_at.desc()).limit(limit))
    ).scalars().all()
    return [view(run) for run in rows]


@router.get("/status")
async def status(db: AsyncSession = Depends(get_db)):
    latest_success = (
        await db.execute(
            select(BatchRun)
            .where(BatchRun.status == "Succeeded")
            .order_by(BatchRun.finished_at.desc())
            .limit(1)
        )
    ).scalars().first()
    latest_run = (
        await db.execute(select(BatchRun).order_by(BatchRun.started_at.desc()).limit(1))
    ).scalars().first()
    data_as_of = (
        await db.execute(select(func.max(CanonicalTransaction.business_date)))
    ).scalar()
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
        finished_at = finished_at.replace(tzinfo=timezone.utc)
    hours_since_success = (utcnow() - finished_at).total_seconds() / 3600
    return {
        "freshness": "stale" if hours_since_success > STALE_AFTER_HOURS else "fresh",
        "hours_since_success": round(hours_since_success, 1),
        "data_as_of": latest_success.business_date,
        "last_success": status_view(latest_success),
        "last_run": status_view(latest_run),
        "last_failed": bool(latest_run and latest_run.status == "Failed"),
    }
