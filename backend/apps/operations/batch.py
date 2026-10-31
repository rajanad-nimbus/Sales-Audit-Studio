"""Daily transaction-log batch: load the day's feeds, then reconcile. Entry point for a scheduler (cron)."""

import logging
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.agents import agent_settings, investigation_service
from apps.database_errors import violates_unique_constraint
from apps.ingest import reconciliation
from apps.ingest import service as ingest
from apps.ingest.common import VALID_TYPES
from apps.ingest.simulation import simulate_feed
from apps.ingest import source_settings
from apps.ingest.shopify import adapter as shopify_adapter
from apps.ingest.limits import MAX_DELIVERY_BYTES, MAX_DELIVERY_RECORDS
from apps.ingest.manifests import validate_business_date
from apps.security.authentication import require_role
from database import get_db
from apps.transactions.models import CanonicalTransaction
from model_base import utcnow
from apps.operations.models import BatchRun
from integrations.ontology.nimbus import audit

router = APIRouter(prefix="/api/batch")
STALE_AFTER_HOURS = 26
RUN_STALE_AFTER_HOURS = 26
logger = logging.getLogger(__name__)


class BatchRunOwnershipLost(RuntimeError):
    """Raised when a worker's batch claim was recovered by another worker."""


def _is_active_batch_conflict(exc: IntegrityError) -> bool:
    """Recognize the one-running-batch-per-date constraint across supported databases."""
    return violates_unique_constraint(
        exc,
        constraint_names={"uq_batch_runs_one_running_per_date"},
        sqlite_messages={"UNIQUE constraint failed: batch_runs.business_date"},
    )


async def run_daily_batch(
    db: AsyncSession,
    business_date: str,
    feeds: dict | None = None,
    trigger: str = "manual",
    rerun: bool = False,
) -> BatchRun:
    validate_batch_date(business_date)
    run = await claim_batch(db, business_date, trigger, rerun)
    try:
        counts, reconciliation_result = await execute_batch(db, run, business_date, feeds)
        await complete_batch(db, run, business_date, counts, reconciliation_result)
    except Exception as exc:  # keep the failure visible instead of losing the run
        return await record_batch_failure(db, run.id, business_date, exc)

    run = await load_batch(db, run.id)
    run = await run_optional_investigation(db, run, business_date)
    return run


def validate_batch_date(business_date: str) -> None:
    """Translate an invalid business date into the batch API's validation response."""
    try:
        validate_business_date(business_date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def claim_batch(db: AsyncSession, business_date: str, trigger: str, rerun: bool) -> BatchRun:
    """Reject conflicting runs, recover stale ownership, and persist a new run claim."""
    done = (
        (
            await db.execute(
                select(BatchRun).where(
                    BatchRun.business_date == business_date, BatchRun.status == "Succeeded"
                )
            )
        )
        .scalars()
        .first()
    )
    if done and not rerun:
        raise HTTPException(
            status_code=409,
            detail=f"Batch for {business_date} already succeeded; pass rerun=true to repeat",
        )
    now = utcnow()
    running = (
        await db.execute(
            select(BatchRun)
            .where(BatchRun.business_date == business_date, BatchRun.status == "Running")
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if running:
        last_activity = running.heartbeat_at or running.started_at
        if last_activity.tzinfo is None:
            last_activity = last_activity.replace(tzinfo=timezone.utc)
        age = now - last_activity
        if age <= timedelta(hours=RUN_STALE_AFTER_HOURS):
            raise HTTPException(
                status_code=409, detail=f"A batch for {business_date} is already running"
            )
        running.status = "Failed"
        running.finished_at = now
        running.error = (
            f"Recovered stale Running batch after {age.total_seconds() / 3600:.1f} hours"
        )
        audit(
            db,
            "BATCH_STALE_RECOVERED",
            None,
            "BatchRun",
            running.id,
            f"Marked abandoned batch {running.id} failed before retrying {business_date}",
            actor="batch-scheduler",
        )

    run = BatchRun(
        id=str(uuid.uuid4()),
        business_date=business_date,
        trigger=trigger,
        status="Running",
        heartbeat_at=now,
    )
    db.add(run)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if _is_active_batch_conflict(exc):
            raise HTTPException(
                status_code=409, detail=f"A batch for {business_date} is already running"
            ) from exc
        raise
    return run


async def execute_batch(db: AsyncSession, run: BatchRun, business_date: str, feeds: dict | None):
    """Load all feeds with per-feed durable checkpoints, then reconcile the date."""
    if feeds is None:
        feeds = await resolve_batch_feeds(db, business_date)
    delivery = f"BATCH-{business_date}-{run.id[:6]}"
    counts = {}
    for source, records in feeds.items():
        counts[source] = await ingest.ingest(db, source, delivery, records)
        await checkpoint_batch_progress(db, run.id, business_date, counts)
    reconciliation_result = await reconciliation.reconcile(db, business_date)
    return counts, reconciliation_result


async def resolve_batch_feeds(db: AsyncSession, business_date: str) -> dict:
    """Build configured simulated feeds and fetch the live Shopify feed when enabled."""
    wanted = await source_settings.batch_sources(db)
    feeds = {
        source: rows for source, rows in simulate_feed(business_date).items() if source in wanted
    }
    batch_start = datetime.fromisoformat(business_date).replace(tzinfo=timezone.utc)
    if "Shopify" in wanted and os.getenv("SHOPIFY_STORE_URL"):
        feeds.update(
            await shopify_adapter.fetch_shopify_feed(
                store_id=os.getenv("SHOPIFY_STORE_ID", "SHOPIFY-MAIN"),
                created_at_min=batch_start,
                created_at_max=batch_start + timedelta(days=1),
            )
        )
    return feeds


async def checkpoint_batch_progress(
    db: AsyncSession, run_id: str, business_date: str, counts: dict
) -> None:
    """Persist progress and prove the worker still owns the active batch run."""
    checkpoint = await db.execute(
        update(BatchRun)
        .where(
            BatchRun.id == run_id,
            BatchRun.status == "Running",
        )
        .values(
            source_counts=dict(counts),
            transactions_loaded=sum(count["canonical_created"] for count in counts.values()),
            heartbeat_at=utcnow(),
        )
    )
    if checkpoint.rowcount != 1:
        raise BatchRunOwnershipLost(f"Batch run {run_id} no longer owns {business_date}")
    await db.commit()


async def complete_batch(
    db: AsyncSession, run: BatchRun, business_date: str, counts: dict, reconciliation_result: dict
) -> None:
    """Mark the owned batch successful and commit its completion audit event."""
    transactions_loaded = sum(count["canonical_created"] for count in counts.values())
    completion = await db.execute(
        update(BatchRun)
        .where(
            BatchRun.id == run.id,
            BatchRun.status == "Running",
        )
        .values(
            source_counts=counts,
            transactions_loaded=transactions_loaded,
            matched_pairs=reconciliation_result["matched_pairs"],
            cases_created=reconciliation_result["cases_created"],
            status="Succeeded",
            finished_at=utcnow(),
            heartbeat_at=None,
        )
    )
    if completion.rowcount != 1:
        raise BatchRunOwnershipLost(f"Batch run {run.id} no longer owns {business_date}")
    audit(
        db,
        "BATCH_COMPLETED",
        None,
        "BatchRun",
        run.id,
        f"Daily batch {business_date}: {transactions_loaded} transactions loaded, "
        f"{reconciliation_result['cases_created']} cases raised",
        actor="batch-scheduler",
    )
    await db.commit()


async def record_batch_failure(
    db: AsyncSession, run_id: str, business_date: str, error: Exception
) -> BatchRun:
    """Persist the failure of a still-owned run without overwriting recovered ownership."""
    await db.rollback()
    failure = await db.execute(
        update(BatchRun)
        .where(
            BatchRun.id == run_id,
            BatchRun.status == "Running",
        )
        .values(status="Failed", finished_at=utcnow(), error=str(error)[:500], heartbeat_at=None)
    )
    if failure.rowcount == 1:
        audit(
            db,
            "BATCH_FAILED",
            None,
            "BatchRun",
            run_id,
            f"Daily batch {business_date} failed: {str(error)[:120]}",
            actor="batch-scheduler",
        )
    await db.commit()
    return await load_batch(db, run_id)


async def load_batch(db: AsyncSession, run_id: str) -> BatchRun:
    """Reload current durable state after a worker commits or recovers its run."""
    return (
        await db.execute(
            select(BatchRun).where(BatchRun.id == run_id).execution_options(populate_existing=True)
        )
    ).scalar_one()


async def run_optional_investigation(
    db: AsyncSession, run: BatchRun, business_date: str
) -> BatchRun:
    """Run optional investigation after batch success; its failure cannot undo the batch."""
    run_id = run.id
    try:
        if (await agent_settings.load(db))["auto_investigate"]:
            await investigation_service.auto_investigate(db, business_date)
    except Exception:
        logger.exception("Automatic investigation failed after batch %s completed", run_id)
        await db.rollback()
        return await load_batch(db, run_id)
    return run


def view(r: BatchRun) -> dict:
    return {
        "id": r.id,
        "business_date": r.business_date,
        "status": r.status,
        "trigger": r.trigger,
        "started_at": r.started_at,
        "heartbeat_at": r.heartbeat_at,
        "finished_at": r.finished_at,
        "transactions_loaded": r.transactions_loaded,
        "matched_pairs": r.matched_pairs,
        "cases_created": r.cases_created,
        "source_counts": r.source_counts,
        "error": r.error,
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
        size = len(
            json.dumps(self.feeds, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        )
        if size > MAX_DELIVERY_BYTES:
            raise ValueError(f"Batch payload exceeds {MAX_DELIVERY_BYTES} bytes")
        return self


@router.post("/run")
async def run_batch(
    req: RunRequest, db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("it"))
):
    bd = req.business_date
    if bd is None:
        earliest = (await db.execute(select(func.min(CanonicalTransaction.business_date)))).scalar()
        bd = (
            (datetime.fromisoformat(earliest) - timedelta(days=1)).strftime("%Y-%m-%d")
            if earliest
            else (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        )
    run = await run_daily_batch(db, bd, req.feeds, trigger="api", rerun=req.rerun)
    return view(run)


@router.get("/runs")
async def runs(
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("finance", "it")),
):
    rows = (
        (
            await db.execute(
                select(BatchRun).order_by(BatchRun.started_at.desc()).limit(min(limit, 100))
            )
        )
        .scalars()
        .all()
    )
    return [view(r) for r in rows]


@router.get("/status")
async def status(db: AsyncSession = Depends(get_db)):
    ok = (
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
    last = (
        (await db.execute(select(BatchRun).order_by(BatchRun.started_at.desc()).limit(1)))
        .scalars()
        .first()
    )
    as_of = (await db.execute(select(func.max(CanonicalTransaction.business_date)))).scalar()
    if not ok:
        return {
            "freshness": "never",
            "data_as_of": as_of,
            "last_success": None,
            "last_run": status_view(last),
            "last_failed": bool(last and last.status == "Failed"),
        }
    finished_at = ok.finished_at
    if finished_at.tzinfo is None:
        finished_at = finished_at.replace(tzinfo=timezone.utc)
    hours = (utcnow() - finished_at).total_seconds() / 3600
    return {
        "freshness": "stale" if hours > STALE_AFTER_HOURS else "fresh",
        "hours_since_success": round(hours, 1),
        "data_as_of": ok.business_date,
        "last_success": status_view(ok),
        "last_run": status_view(last),
        "last_failed": bool(last and last.status == "Failed"),
    }
