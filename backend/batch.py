"""Daily transaction-log batch: load the day's feeds, then reconcile. Entry point for a scheduler (cron)."""
import os
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import agent_settings
import ingest
import investigation_service
import shopify_adapter
from auth import require_role
from database import get_db
from models import BatchRun, CanonicalTransaction, utcnow
from nimbus import audit

router = APIRouter(prefix="/api/batch")
STALE_AFTER_HOURS = 26


async def run_daily_batch(db: AsyncSession, business_date: str, feeds: dict | None = None,
                          trigger: str = "manual", rerun: bool = False) -> BatchRun:
    done = (await db.execute(select(BatchRun).where(
        BatchRun.business_date == business_date, BatchRun.status == "Succeeded"))).scalars().first()
    if done and not rerun:
        raise HTTPException(status_code=409, detail=f"Batch for {business_date} already succeeded; pass rerun=true to repeat")

    run = BatchRun(id=str(uuid.uuid4()), business_date=business_date, trigger=trigger, status="Running")
    db.add(run)
    await db.commit()
    try:
        if feeds is None:
            feeds = ingest.simulate_feed(business_date)
            bd = datetime.fromisoformat(business_date).replace(tzinfo=timezone.utc)
            if os.getenv("SHOPIFY_STORE_URL"):  # a configured connector must not fail silently
                feeds.update(await shopify_adapter.fetch_shopify_feed(
                    store_id=os.getenv("SHOPIFY_STORE_ID", "SHOPIFY-MAIN"),
                    created_at_min=bd, created_at_max=bd + timedelta(days=1)))
        delivery = f"BATCH-{business_date}-{run.id[:6]}"
        counts = {src: await ingest.ingest(db, src, delivery, recs) for src, recs in feeds.items()}
        recon = await ingest.reconcile(db, business_date)
        run.source_counts = counts
        run.transactions_loaded = sum(c["canonical_created"] for c in counts.values())
        run.matched_pairs, run.cases_created = recon["matched_pairs"], recon["cases_created"]
        run.status, run.finished_at = "Succeeded", utcnow()
        audit(db, "BATCH_COMPLETED", None, "BatchRun", run.id,
              f"Daily batch {business_date}: {run.transactions_loaded} transactions loaded, {run.cases_created} cases raised",
              actor="batch-scheduler")
        await db.commit()
        if (await agent_settings.load(db))["auto_investigate"]:
            await investigation_service.auto_investigate(db, business_date)
    except Exception as e:  # keep the failure visible instead of losing the run
        await db.rollback()
        run = (await db.execute(select(BatchRun).where(BatchRun.id == run.id))).scalar_one()
        run.status, run.finished_at, run.error = "Failed", utcnow(), str(e)[:500]
        audit(db, "BATCH_FAILED", None, "BatchRun", run.id, f"Daily batch {business_date} failed: {str(e)[:120]}",
              actor="batch-scheduler")
        await db.commit()
    return run


def view(r: BatchRun) -> dict:
    return {"id": r.id, "business_date": r.business_date, "status": r.status, "trigger": r.trigger,
            "started_at": r.started_at, "finished_at": r.finished_at, "transactions_loaded": r.transactions_loaded,
            "matched_pairs": r.matched_pairs, "cases_created": r.cases_created, "source_counts": r.source_counts,
            "error": r.error}


class RunRequest(BaseModel):
    business_date: str | None = None
    rerun: bool = False
    feeds: dict[str, list[dict]] | None = None


@router.post("/run")
async def run_batch(req: RunRequest, db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("it"))):
    bd = req.business_date
    if not bd:
        earliest = (await db.execute(select(func.min(CanonicalTransaction.business_date)))).scalar()
        bd = (datetime.fromisoformat(earliest) - timedelta(days=1)).strftime("%Y-%m-%d") if earliest \
            else (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    run = await run_daily_batch(db, bd, req.feeds, trigger="api", rerun=req.rerun)
    return view(run)


@router.get("/runs")
async def runs(limit: int = 30, db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("finance", "it"))):
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
