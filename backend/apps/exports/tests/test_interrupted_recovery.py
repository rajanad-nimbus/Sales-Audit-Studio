import asyncio
import importlib.util
from datetime import timedelta
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from models import AuditEvent, Base, ExportBatch, ExportDestination, ExportItem, utcnow
from sqlalchemy import create_engine, inspect, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.exports import routes


def test_recovery_fences_late_worker_and_keeps_reservation(tmp_path, monkeypatch):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
        started, finish = asyncio.Event(), asyncio.Event()

        async def deliver(_destination, batch):
            started.set()
            await finish.wait()
            batch.status = "Sent"

        monkeypatch.setattr(routes, "deliver", deliver)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with sessions() as db:
                db.add(ExportDestination(id="d", name="ERP", kind="file", format="json", config={}))
                db.add(
                    ExportBatch(
                        id="b",
                        destination_id="d",
                        dataset="resolutions",
                        status="Failed",
                        idempotency_key="k",
                        created_by="it",
                    )
                )
                db.add(
                    ExportItem(
                        id="i",
                        batch_id="b",
                        destination_id="d",
                        dataset="resolutions",
                        object_id="c",
                        object_version="v1",
                        state="Pending",
                    )
                )
                await db.commit()
            async with sessions() as worker, sessions() as operator:
                task = asyncio.create_task(routes.retry("b", worker, {"user": "it"}))
                try:
                    await asyncio.wait_for(started.wait(), timeout=5)
                    with pytest.raises(HTTPException, match="too recent"):
                        await routes.recover_interrupted_batch(
                            "b",
                            routes.InterruptedExportRecovery(
                                worker_stopped=True, evidence="checked"
                            ),
                            operator,
                            {"user": "it"},
                        )
                    await operator.execute(
                        update(ExportBatch)
                        .where(ExportBatch.id == "b")
                        .values(attempt_started_at=utcnow() - timedelta(minutes=10))
                    )
                    await operator.commit()
                    with pytest.raises(HTTPException, match="Confirm the delivery worker"):
                        await routes.recover_interrupted_batch(
                            "b",
                            routes.InterruptedExportRecovery(
                                worker_stopped=False, evidence="checked"
                            ),
                            operator,
                            {"user": "it"},
                        )
                    result = await routes.recover_interrupted_batch(
                        "b",
                        routes.InterruptedExportRecovery(
                            worker_stopped=True, evidence="Worker host stopped; incident 123"
                        ),
                        operator,
                        {"user": "it"},
                    )
                    assert result["status"] == "Failed"
                finally:
                    finish.set()
                with pytest.raises(HTTPException, match="superseded"):
                    await task
            async with sessions() as db:
                batch = await db.get(ExportBatch, "b")
                assert batch.status == "Failed" and batch.delivery_attempt_id is None
                assert (await db.get(ExportItem, "i")).state == "Pending"
                event = (
                    await db.execute(
                        select(AuditEvent).where(
                            AuditEvent.event_type == "EXPORT_ATTEMPT_RECOVERED"
                        )
                    )
                ).scalar_one()
                assert event.after_state["evidence"] == "Worker host stopped; incident 123"
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_attempt_migration_backfills_legacy_pending_and_downgrades():
    path = Path(__file__).resolve().parents[3] / "alembic/versions/0040_export_attempts.py"
    spec = importlib.util.spec_from_file_location("export_attempt_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE export_batches (status VARCHAR(20), created_at TIMESTAMP)")
            )
            connection.execute(
                text(
                    "INSERT INTO export_batches VALUES ('Pending', '2026-01-01 00:00:00'), ('Failed', '2026-01-01 00:00:00')"
                )
            )
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                rows = connection.execute(
                    text("SELECT status, attempt_started_at FROM export_batches ORDER BY status")
                ).all()
                assert rows[0][1] is None and rows[1][1] is not None
                migration.downgrade()
            assert {
                column["name"] for column in inspect(connection).get_columns("export_batches")
            } == {"status", "created_at"}
    finally:
        engine.dispose()


def test_late_result_cannot_finish_a_new_pending_attempt():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add(ExportDestination(id="d", name="ERP", kind="file", format="json", config={}))
                db.add(
                    ExportBatch(
                        id="b",
                        destination_id="d",
                        dataset="resolutions",
                        status="Pending",
                        idempotency_key="k",
                        created_by="it",
                        delivery_attempt_id="new",
                    )
                )
                await db.commit()
                old = ExportBatch(
                    id="b",
                    destination_id="d",
                    dataset="resolutions",
                    status="Sent",
                    idempotency_key="k",
                    created_by="it",
                    delivery_attempt_id="old",
                )
                with pytest.raises(HTTPException, match="superseded"):
                    await routes.finish_delivery(db, old, "ERP", retrying=True)
                current = await db.get(ExportBatch, "b")
                assert current.status == "Pending" and current.delivery_attempt_id == "new"
        finally:
            await engine.dispose()

    asyncio.run(run())
