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
from apps.ingest import reconciliation
from apps.ingest import service as ingest
from apps.ingest.common import VALID_TYPES
from apps.ingest.simulation import simulate_feed
from apps.ingest import shopify_adapter, source_settings
from apps.ingest.limits import MAX_DELIVERY_BYTES, MAX_DELIVERY_RECORDS
from apps.ingest.manifests import validate_business_date
from auth import require_role
from database import get_db
from models import BatchRun, CanonicalTransaction, utcnow
from integrations.ontology.nimbus import audit

router = APIRouter(prefix="/api/batch")
STALE_AFTER_HOURS = 26
RUN_STALE_AFTER_HOURS = 26
logger = logging.getLogger(__name__)


class BatchRunOwnershipLost(RuntimeError):
    """Raised when a worker's batch claim was recovered by another worker."""


async def run_daily_batch(db: AsyncSession, business_date: str, feeds: dict | None = None,
                          trigger: str = "manual", rerun: bool = False) -> BatchRun:
    try:
        validate_business_date(business_date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    done = (await db.execute(select(BatchRun).where(
        BatchRun.business_date == business_date, BatchRun.status == "Succeeded"))).scalars().first()
    if done and not rerun:
        raise HTTPException(status_code=409, detail=f"Batch for {business_date} already succeeded; pass rerun=true to repeat")
    now = utcnow()
    running = (await db.execute(select(BatchRun).where(
        BatchRun.business_date == business_date, BatchRun.status == "Running"
    ).with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
    if running:
        last_activity = running.heartbeat_at or running.started_at
        if last_activity.tzinfo is None:
            last_activity = last_activity.replace(tzinfo=timezone.utc)
        age = now - last_activity
        if age <= timedelta(hours=RUN_STALE_AFTER_HOURS):
            raise HTTPException(status_code=409, detail=f"A batch for {business_date} is already running")
        running.status = "Failed"
        running.finished_at = now
        running.error = f"Recovered stale Running batch after {age.total_seconds() / 3600:.1f} hours"
        audit(db, "BATCH_STALE_RECOVERED", None, "BatchRun", running.id,
              f"Marked abandoned batch {running.id} failed before retrying {business_date}",
              actor="batch-scheduler")

    run = BatchRun(id=str(uuid.uuid4()), business_date=business_date, trigger=trigger, status="Running",
                   heartbeat_at=now)
    run_id = run.id
    db.add(run)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"A batch for {business_date} is already running") from exc
    try:
        if feeds is None:
            wanted = await source_settings.batch_sources(db)   # sources switched off or kept out of the batch are skipped
            feeds = {src: rows for src, rows in simulate_feed(business_date).items() if src in wanted}
            bd = datetime.fromisoformat(business_date).replace(tzinfo=timezone.utc)
            if "Shopify" in wanted and os.getenv("SHOPIFY_STORE_URL"):  # a configured connector must not fail silently
                feeds.update(await shopify_adapter.fetch_shopify_feed(
                    store_id=os.getenv("SHOPIFY_STORE_ID", "SHOPIFY-MAIN"),
                    created_at_min=bd, created_at_max=bd + timedelta(days=1)))
        delivery = f"BATCH-{business_date}-{run.id[:6]}"
        counts = {}
        for src, records in feeds.items():
            counts[src] = await ingest.ingest(db, src, delivery, records)
            checkpoint = await db.execute(
                update(BatchRun)
                .where(BatchRun.id == run_id, BatchRun.status == "Running")
                .values(
                    source_counts=dict(counts),
                    transactions_loaded=sum(count["canonical_created"] for count in counts.values()),
                    heartbeat_at=utcnow(),
                )
            )
            if checkpoint.rowcount != 1:
                raise BatchRunOwnershipLost(f"Batch run {run_id} no longer owns {business_date}")
            await db.commit()
        recon = await reconciliation.reconcile(db, business_date)
        finished_at = utcnow()
        completion = await db.execute(
            update(BatchRun)
            .where(BatchRun.id == run_id, BatchRun.status == "Running")
            .values(
                source_counts=counts,
                transactions_loaded=sum(c["canonical_created"] for c in counts.values()),
                matched_pairs=recon["matched_pairs"],
                cases_created=recon["cases_created"],
                status="Succeeded",
                finished_at=finished_at,
                heartbeat_at=None,
            )
        )
        if completion.rowcount != 1:
            raise BatchRunOwnershipLost(f"Batch run {run_id} no longer owns {business_date}")
        audit(db, "BATCH_COMPLETED", None, "BatchRun", run_id,
              f"Daily batch {business_date}: {sum(c['canonical_created'] for c in counts.values())} transactions loaded, {recon['cases_created']} cases raised",
              actor="batch-scheduler")
        await db.commit()
    except Exception as e:  # keep the failure visible instead of losing the run
        await db.rollback()
        failure = await db.execute(
            update(BatchRun)
            .where(BatchRun.id == run_id, BatchRun.status == "Running")
            .values(status="Failed", finished_at=utcnow(), error=str(e)[:500], heartbeat_at=None)
        )
        if failure.rowcount == 1:
            audit(db, "BATCH_FAILED", None, "BatchRun", run_id,
                  f"Daily batch {business_date} failed: {str(e)[:120]}", actor="batch-scheduler")
        await db.commit()
        run = (await db.execute(select(BatchRun).where(BatchRun.id == run_id)
                                .execution_options(populate_existing=True))).scalar_one()
        return run

    run = (await db.execute(select(BatchRun).where(BatchRun.id == run_id)
                            .execution_options(populate_existing=True))).scalar_one()

    # Investigation is a separate, optional operation. The batch and its audit event
    # have already committed, so an investigation failure cannot undo their success.
    try:
        if (await agent_settings.load(db))["auto_investigate"]:
            await investigation_service.auto_investigate(db, business_date)
    except Exception:
        logger.exception("Automatic investigation failed after batch %s completed", run_id)
        await db.rollback()
        run = (await db.execute(select(BatchRun).where(BatchRun.id == run_id))).scalar_one()
    return run


def view(r: BatchRun) -> dict:
    return {"id": r.id, "business_date": r.business_date, "status": r.status, "trigger": r.trigger,
            "started_at": r.started_at, "heartbeat_at": r.heartbeat_at, "finished_at": r.finished_at,
            "transactions_loaded": r.transactions_loaded,
            "matched_pairs": r.matched_pairs, "cases_created": r.cases_created, "source_counts": r.source_counts,
            "error": r.error}


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
        size = len(json.dumps(self.feeds, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        if size > MAX_DELIVERY_BYTES:
            raise ValueError(f"Batch payload exceeds {MAX_DELIVERY_BYTES} bytes")
        return self


@router.post("/run")
async def run_batch(req: RunRequest, db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("it"))):
    bd = req.business_date
    if bd is None:
        earliest = (await db.execute(select(func.min(CanonicalTransaction.business_date)))).scalar()
        bd = (datetime.fromisoformat(earliest) - timedelta(days=1)).strftime("%Y-%m-%d") if earliest \
            else (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    run = await run_daily_batch(db, bd, req.feeds, trigger="api", rerun=req.rerun)
    return view(run)


@router.get("/runs")
async def runs(limit: Annotated[int, Query(ge=1, le=100)] = 30, db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("finance", "it"))):
    rows = (await db.execute(select(BatchRun).order_by(BatchRun.started_at.desc()).limit(min(limit, 100)))).scalars().all()
    return [view(r) for r in rows]


@router.get("/status")
async def status(db: AsyncSession = Depends(get_db)):
    ok = (await db.execute(select(BatchRun).where(BatchRun.status == "Succeeded")
                           .order_by(BatchRun.finished_at.desc()).limit(1))).scalars().first()
    last = (await db.execute(select(BatchRun).order_by(BatchRun.started_at.desc()).limit(1))).scalars().first()
    as_of = (await db.execute(select(func.max(CanonicalTransaction.business_date)))).scalar()
    if not ok:
        return {"freshness": "never", "data_as_of": as_of, "last_success": None, "last_run": view(last) if last else None}
    hours = (utcnow() - ok.finished_at).total_seconds() / 3600
    return {"freshness": "stale" if hours > STALE_AFTER_HOURS else "fresh", "hours_since_success": round(hours, 1),
            "data_as_of": ok.business_date, "last_success": view(ok), "last_run": view(last),
            "last_failed": bool(last and last.status == "Failed")}
