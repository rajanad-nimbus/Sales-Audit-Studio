import asyncio
import hashlib
import json

import pytest
from fastapi import HTTPException
from models import AuditEvent, Base, ExportBatch, ExportDestination, ExportItem
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.exports import routes


def test_active_retry_rejects_other_retry_and_cancellation(tmp_path, monkeypatch):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
        started, finish = asyncio.Event(), asyncio.Event()
        deliveries = []

        async def deliver(_destination, batch):
            deliveries.append(batch.id)
            started.set()
            await finish.wait()
            batch.status = "Sent"

        monkeypatch.setattr(routes, "deliver", deliver)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session = async_sessionmaker(engine, expire_on_commit=False)
            async with session() as db:
                db.add(ExportDestination(id="d1", name="ERP", kind="file", format="json", config={}))
                db.add(ExportBatch(id="b1", destination_id="d1", dataset="resolutions", status="Failed",
                                   idempotency_key="key", created_by="it", retries=0))
                db.add(ExportItem(id="e1", batch_id="b1", destination_id="d1", dataset="resolutions",
                                  object_id="c1", object_version="v1", state="Pending"))
                await db.commit()

            async with session() as first, session() as second:
                task = asyncio.create_task(routes.retry("b1", first, {"user": "it"}))
                try:
                    await asyncio.wait_for(started.wait(), timeout=5)
                    with pytest.raises(HTTPException) as retry_error:
                        await routes.retry("b1", second, {"user": "it"})
                    assert retry_error.value.status_code == 409
                    with pytest.raises(HTTPException) as cancel_error:
                        await routes.cancel_batch("b1", second, {"user": "it"})
                    assert cancel_error.value.status_code == 409
                    with pytest.raises(HTTPException) as resolution_error:
                        await routes.reconcile_failed_batch("b1", routes.FailedExportResolution(
                            outcome="received", evidence="Receiver confirmation"), second, {"user": "it"})
                    assert resolution_error.value.status_code == 409
                finally:
                    finish.set()
                    result = await task
                assert result["status"] == "Sent"
                assert result["retries"] == 1
                assert deliveries == ["b1"]

            async with session() as db:
                item = await db.get(ExportItem, "e1")
                assert item.state == "Delivered"
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_disabled_destination_cannot_retry_failed_batch(tmp_path, monkeypatch):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
        deliveries = []

        async def deliver(*_args):
            deliveries.append(True)

        monkeypatch.setattr(routes, "deliver", deliver)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session = async_sessionmaker(engine, expire_on_commit=False)
            async with session() as db:
                db.add(ExportDestination(id="d1", name="ERP", kind="file", format="json", config={}, enabled=False))
                db.add(ExportBatch(id="b1", destination_id="d1", dataset="resolutions", status="Failed",
                                   idempotency_key="key", created_by="it", retries=0))
                await db.commit()
                executed = []
                original_execute = db.execute

                async def capture_locked_select(statement, *args, **kwargs):
                    if getattr(statement, "_for_update_arg", None) is not None:
                        executed.append(statement)
                    return await original_execute(statement, *args, **kwargs)

                monkeypatch.setattr(db, "execute", capture_locked_select)
                with pytest.raises(HTTPException) as error:
                    await routes.retry("b1", db, {"user": "it"})
                assert len(executed) == 1
                assert error.value.status_code == 409
                assert error.value.detail == "Destination is disabled"
                batch = await db.get(ExportBatch, "b1")
                assert batch.status == "Failed" and batch.retries == 0
                assert deliveries == []
        finally:
            await engine.dispose()

    asyncio.run(run())


@pytest.mark.parametrize("more_events", [False, True])
def test_failed_audit_batch_cannot_be_recreated_via_run(tmp_path, monkeypatch, more_events):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
        deliveries = []

        async def deliver(*_args):
            deliveries.append(True)

        monkeypatch.setattr(routes, "deliver", deliver)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session = async_sessionmaker(engine, expire_on_commit=False)
            async with session() as db:
                db.add(ExportDestination(id="d1", name="ERP", kind="file", format="json", config={}))
                db.add(AuditEvent(id="a1", seq=1, event_type="TEST", actor="it", object_type="test",
                                  object_id="object", action_description="test"))
                if more_events:
                    db.add(AuditEvent(id="a2", seq=2, event_type="TEST", actor="it", object_type="test",
                                      object_id="object", action_description="later event"))
                key = hashlib.sha256(json.dumps(["d1", "audit", [], 1, 1]).encode()).hexdigest()
                db.add(ExportBatch(id="b1", destination_id="d1", dataset="audit", status="Failed",
                                   idempotency_key=key, created_by="it", payload="original", payload_hash="hash"))
                await db.commit()
                with pytest.raises(HTTPException) as error:
                    await routes.run_export(routes.RunIn(destination_id="d1", dataset="audit"), db, {"user": "it"})
                assert error.value.status_code == 409
                assert "resolve the existing batch" in error.value.detail
                assert (await db.get(ExportBatch, "b1")).payload == "original"
                assert deliveries == []
        finally:
            await engine.dispose()

    asyncio.run(run())
