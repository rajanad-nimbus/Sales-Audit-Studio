import asyncio
from datetime import timedelta

import pytest
from apps.ingest.contracts import json_payload_size
from apps.operations import batch, batch_routes
from apps.operations.batch_routes import RunRequest
from models import AuditEvent, Base, BatchRun, utcnow
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


def test_batch_feed_size_validation_counts_utf8_and_enforces_limit(monkeypatch):
    feeds = {"POS": [{"source_record_id": "sale", "note": "é"}]}
    size = json_payload_size(feeds, 10_000)
    monkeypatch.setattr(batch_routes, "MAX_DELIVERY_BYTES", size)

    assert RunRequest(feeds=feeds).feeds == feeds

    monkeypatch.setattr(batch_routes, "MAX_DELIVERY_BYTES", size - 1)
    with pytest.raises(ValidationError, match="Batch payload exceeds"):
        RunRequest(feeds=feeds)


def test_successful_batch_remains_successful_if_investigation_fails(monkeypatch, caplog):
    async def reconcile(_db, _date):
        return {"matched_pairs": 0, "cases_created": 0}

    async def settings(_db):
        return {"auto_investigate": True}

    async def investigate(_db, _date):
        raise RuntimeError("investigation unavailable")

    monkeypatch.setattr(batch.reconciliation, "reconcile", reconcile)
    monkeypatch.setattr(batch.agent_settings, "load", settings)
    monkeypatch.setattr(batch.investigation_service, "auto_investigate", investigate)

    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            session = async_sessionmaker(engine, expire_on_commit=False)
            async with session() as db:
                result = await batch.run_daily_batch(db, "2026-10-01", feeds={})
                assert result.status == "Succeeded"
                assert result.error is None
                events = (await db.execute(select(AuditEvent.event_type))).scalars().all()
                assert events == ["BATCH_COMPLETED"]
                stored = (await db.execute(select(BatchRun))).scalar_one()
                assert stored.status == "Succeeded"
        finally:
            await engine.dispose()

    asyncio.run(run())
    assert "Automatic investigation failed after batch" in caplog.text


def test_failed_batch_is_recorded_after_rollback(monkeypatch):
    async def ingest(_db, _source, _delivery, _records):
        raise RuntimeError("source unavailable")

    monkeypatch.setattr(batch.ingest, "ingest", ingest)

    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            session = async_sessionmaker(engine, expire_on_commit=False)
            async with session() as db:
                result = await batch.run_daily_batch(db, "2026-10-01", feeds={"POS": []})
                assert result.status == "Failed"
                assert result.error == "Batch failed (RuntimeError)"
                events = (await db.execute(select(AuditEvent.event_type))).scalars().all()
                assert events == ["BATCH_FAILED"]
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_cancelled_batch_is_recorded_failed_before_cancellation_propagates(monkeypatch):
    started = asyncio.Event()

    async def wait_for_shutdown(_db, _source, _delivery, _records):
        started.set()
        await asyncio.Future()

    monkeypatch.setattr(batch.ingest, "ingest", wait_for_shutdown)

    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with sessions() as db:
                task = asyncio.create_task(
                    batch.run_daily_batch(db, "2026-10-01", feeds={"POS": []})
                )
                await started.wait()
                task.cancel()

                with pytest.raises(asyncio.CancelledError):
                    await task

                stored = (await db.execute(select(BatchRun))).scalar_one()
                assert stored.status == "Failed"
                assert stored.error == "Batch failed (CancelledError)"
                events = (await db.execute(select(AuditEvent.event_type))).scalars().all()
                assert events == ["BATCH_FAILED"]
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_failure_preserves_counts_for_feeds_already_committed(monkeypatch):
    async def ingest(_db, source, _delivery, _records):
        if source == "Processor":
            raise RuntimeError("processor unavailable")
        return {"received": 3, "canonical_created": 2, "quarantined": 1}

    monkeypatch.setattr(batch.ingest, "ingest", ingest)

    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            session = async_sessionmaker(engine, expire_on_commit=False)
            async with session() as db:
                result = await batch.run_daily_batch(
                    db, "2026-10-01", feeds={"POS": [], "Processor": []}
                )
                assert result.status == "Failed"
                assert result.error == "Batch failed (RuntimeError)"
                assert result.source_counts == {
                    "POS": {"received": 3, "canonical_created": 2, "quarantined": 1}
                }
                assert result.transactions_loaded == 2
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_batch_failure_does_not_persist_sensitive_exception_text(monkeypatch):
    secret = "https://feed.example/?token=top-secret"

    async def ingest(_db, _source, _delivery, _records):
        raise RuntimeError(f"failed to fetch {secret}")

    monkeypatch.setattr(batch.ingest, "ingest", ingest)

    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            session = async_sessionmaker(engine, expire_on_commit=False)
            async with session() as db:
                result = await batch.run_daily_batch(db, "2026-10-01", feeds={"POS": []})
                assert result.error == "Batch failed (RuntimeError)"
                event = (await db.execute(select(AuditEvent))).scalar_one()
                assert secret not in event.action_description
                assert result.error == event.action_description.removeprefix(
                    "Daily batch 2026-10-01 failed: "
                )
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_recovered_batch_worker_cannot_checkpoint_or_report_success(monkeypatch):
    async def ingest(db, _source, _delivery, _records):
        run_id = (await db.execute(select(BatchRun.id).where(BatchRun.status == "Running"))).scalar_one()
        await db.execute(update(BatchRun).where(BatchRun.id == run_id).values(
            status="Failed", error="recovered by replacement worker"
        ))
        await db.commit()
        return {"received": 1, "canonical_created": 1}

    monkeypatch.setattr(batch.ingest, "ingest", ingest)

    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            session = async_sessionmaker(engine, expire_on_commit=False)
            async with session() as db:
                result = await batch.run_daily_batch(db, "2026-10-01", feeds={"POS": [{}]})
                assert result.status == "Failed"
                assert result.error == "recovered by replacement worker"
                events = (await db.execute(select(AuditEvent.event_type))).scalars().all()
                assert "BATCH_COMPLETED" not in events
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_recovered_batch_worker_cannot_commit_reconciliation(monkeypatch):
    async def reconcile(db, _business_date):
        run_id = (await db.execute(select(BatchRun.id).where(BatchRun.status == "Running"))).scalar_one()
        await db.execute(update(BatchRun).where(BatchRun.id == run_id).values(
            status="Failed", error="recovered during reconciliation"
        ))
        await db.commit()
        return {"matched_pairs": 1, "cases_created": 2}

    monkeypatch.setattr(batch.reconciliation, "reconcile", reconcile)

    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            session = async_sessionmaker(engine, expire_on_commit=False)
            async with session() as db:
                result = await batch.run_daily_batch(db, "2026-10-01", feeds={})
                assert result.status == "Failed"
                assert result.error == "recovered during reconciliation"
                assert result.matched_pairs == 0
                assert result.cases_created == 0
                events = (await db.execute(select(AuditEvent.event_type))).scalars().all()
                assert "BATCH_COMPLETED" not in events
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_active_heartbeat_prevents_recovery_of_long_running_batch():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            session = async_sessionmaker(engine, expire_on_commit=False)
            async with session() as db:
                now = utcnow()
                db.add(BatchRun(
                    id="active-run", business_date="2026-10-01", status="Running",
                    started_at=now - timedelta(days=3), heartbeat_at=now - timedelta(minutes=1),
                ))
                await db.commit()
                try:
                    await batch.run_daily_batch(db, "2026-10-01", feeds={})
                except Exception as exc:
                    assert getattr(exc, "status_code", None) == 409
                else:
                    raise AssertionError("active batch should keep its claim")
                stored = (await db.execute(select(BatchRun))).scalar_one()
                assert stored.status == "Running"
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_stale_heartbeat_is_recovered_before_retry():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            session = async_sessionmaker(engine, expire_on_commit=False)
            async with session() as db:
                now = utcnow()
                db.add(BatchRun(
                    id="stale-run", business_date="2026-10-01", status="Running",
                    started_at=now - timedelta(days=3), heartbeat_at=now - timedelta(days=2),
                ))
                await db.commit()
                result = await batch.run_daily_batch(db, "2026-10-01", feeds={})
                assert result.status == "Succeeded"
                old_run = (await db.execute(select(BatchRun).where(BatchRun.id == "stale-run"))).scalar_one()
                assert old_run.status == "Failed"
                assert "Recovered stale Running batch" in old_run.error
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_latest_successful_batch_lookup_uses_composite_index():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
                plan = await connection.exec_driver_sql(
                    "EXPLAIN QUERY PLAN SELECT id FROM batch_runs "
                    "WHERE status = 'Succeeded' ORDER BY finished_at DESC LIMIT 1"
                )
                details = " ".join(row[3] for row in plan.fetchall())
                assert "ix_batch_runs_status_finished_at" in details
        finally:
            await engine.dispose()

    asyncio.run(run())
