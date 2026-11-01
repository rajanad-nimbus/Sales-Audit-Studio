"""Daily transaction-log batch: load the day's feeds, then reconcile. Entry point for a scheduler (cron)."""

import asyncio
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.agents import agent_settings, investigation_service
from apps.database_errors import violates_unique_constraint
from apps.ingest import reconciliation
from apps.ingest import service as ingest
from apps.ingest.simulation import simulate_feed
from apps.ingest import source_settings
from apps.ingest.shopify import adapter as shopify_adapter
from apps.ingest.manifests import validate_business_date
from model_base import utcnow
from apps.operations.models import BatchRun
from integrations.ontology.nimbus import audit

RUN_STALE_AFTER_HOURS = 26
logger = logging.getLogger(__name__)


class BatchRunOwnershipLost(RuntimeError):
    """Raised when a worker's batch claim was recovered by another worker."""


def safe_batch_failure(error: BaseException) -> str:
    """Describe a failed run without persisting source payloads or driver details."""
    if isinstance(error, reconciliation.ReconciliationCapacityError):
        return str(error)
    if isinstance(error, BatchRunOwnershipLost):
        return "Batch ownership was lost; another worker recovered this run"
    if isinstance(error, HTTPException):
        return f"Batch failed with HTTP status {error.status_code}"
    return f"Batch failed ({type(error).__name__})"


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
    except asyncio.CancelledError as exc:
        await asyncio.shield(record_batch_failure(db, run.id, business_date, exc))
        raise
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
    db: AsyncSession, run_id: str, business_date: str, error: BaseException
) -> BatchRun:
    """Persist the failure of a still-owned run without overwriting recovered ownership."""
    await db.rollback()
    safe_error = safe_batch_failure(error)
    failure = await db.execute(
        update(BatchRun)
        .where(
            BatchRun.id == run_id,
            BatchRun.status == "Running",
        )
        .values(status="Failed", finished_at=utcnow(), error=safe_error, heartbeat_at=None)
    )
    if failure.rowcount == 1:
        audit(
            db,
            "BATCH_FAILED",
            None,
            "BatchRun",
            run_id,
            f"Daily batch {business_date} failed: {safe_error}",
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
