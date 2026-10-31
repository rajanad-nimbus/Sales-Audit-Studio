import asyncio
import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from apps.exports import routes
from models import Base, Case, ExportBatch, ExportDestination, TestSinkReceipt as SinkReceipt


def request_for(body: bytes) -> Request:
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}
    return Request({"type": "http", "method": "POST", "path": "/api/exports/_sink", "headers": []}, receive)


def test_sink_persists_retry_receipts_and_rejects_key_reuse(tmp_path):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            key, body = "a" * 64, b"first payload"
            envelope = routes.sign_envelope(routes.SINK_SECRET, "resolutions", key, body)
            async with sessions() as db:
                first = await routes.sink(request_for(body), routes.sign(routes.SINK_SECRET, body), key, "resolutions", db, envelope)
            async with sessions() as db:
                assert await routes.sink(request_for(body), routes.sign(routes.SINK_SECRET, body), key, "resolutions", db, envelope) == first
                assert (await db.execute(select(func.count()).select_from(SinkReceipt))).scalar_one() == 1
                other = b"different payload"
                with pytest.raises(HTTPException) as error:
                    await routes.sink(request_for(other), routes.sign(routes.SINK_SECRET, other), key, "resolutions", db,
                                      routes.sign_envelope(routes.SINK_SECRET, "resolutions", key, other))
                assert error.value.status_code == 409
                with pytest.raises(HTTPException) as error:
                    await routes.sink(request_for(body), routes.sign(routes.SINK_SECRET, body), key, "adjustments", db,
                                      routes.sign_envelope(routes.SINK_SECRET, "adjustments", key, body))
                assert error.value.status_code == 409
                with pytest.raises(HTTPException) as error:
                    await routes.sink(request_for(body), "bad", key, "resolutions", db, envelope)
                assert error.value.status_code == 401
                for changed_key, changed_dataset in (("b" * 64, "resolutions"), (key, "adjustments")):
                    with pytest.raises(HTTPException) as error:
                        await routes.sink(request_for(body), routes.sign(routes.SINK_SECRET, body), changed_key, changed_dataset, db, envelope)
                    assert error.value.status_code == 401
                with pytest.raises(HTTPException) as error:
                    await routes.sink(request_for(body), routes.sign(routes.SINK_SECRET, body), key, "resolutions", db, "é")
                assert error.value.status_code == 401
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_sink_rejects_request_body_over_limit(monkeypatch):
    # The limit is checked while reading, before signature or database work.
    monkeypatch.setattr(routes, "MAX_SINK_BODY_BYTES", 4)

    async def reject():
        request = request_for(b"12345")
        with pytest.raises(HTTPException) as error:
            await routes.sink(request, "", "a" * 64, "resolutions", None, "")
        assert error.value.status_code == 413

    asyncio.run(reject())


def test_test_sink_is_disabled_and_not_seeded_in_production(monkeypatch, tmp_path):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("NIMBUS_ENABLE_TEST_SINK", "true")
    assert not routes.test_sink_enabled()

    async def reject_request():
        with pytest.raises(HTTPException) as error:
            await routes.require_test_sink_enabled()
        assert error.value.status_code == 404

    asyncio.run(reject_request())

    async def seed_defaults():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'production.db'}")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                await routes.ensure_defaults(db)
                names = (await db.execute(select(ExportDestination.name))).scalars().all()
                assert names == ["ERP/GL file drop"]
                identifier = (await db.execute(select(ExportDestination.id))).scalar_one()
                await routes.ensure_defaults(db)
                assert (await db.execute(select(ExportDestination.id))).scalar_one() == identifier
        finally:
            await engine.dispose()

    asyncio.run(seed_defaults())


def test_sink_receipt_migration_upgrades_and_downgrades():
    path = Path(__file__).resolve().parents[3] / "alembic/versions/0041_test_sink_receipts.py"
    spec = importlib.util.spec_from_file_location("sink_receipt_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                assert "test_sink_receipts" in inspect(connection).get_table_names()
                migration.downgrade()
            assert "test_sink_receipts" not in inspect(connection).get_table_names()
    finally:
        engine.dispose()


def test_rebuilt_cancelled_batch_has_distinct_key(monkeypatch, tmp_path):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")

        async def failed_delivery(_destination, batch):
            batch.status = "Failed"

        monkeypatch.setattr(routes, "deliver", failed_delivery)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add(ExportDestination(id="d", name="ERP", kind="webhook", format="json", config={}))
                db.add(Case(id="case", case_number="C1", status="Closed", case_type="test", store_id="S1", business_date="2026-10-01"))
                await db.commit()
                first = await routes.run_export(routes.RunIn(destination_id="d", dataset="resolutions"), db, {"user": "it"})
                original = (await db.get(ExportBatch, first["id"])).idempotency_key
                await routes.cancel_batch(first["id"], db, {"user": "it"}, routes.FailedExportResolution(
                    outcome="not_received", evidence="Receiver confirmed no receipt"))
                second = await routes.run_export(routes.RunIn(destination_id="d", dataset="resolutions"), db, {"user": "it"})
                assert (await db.get(ExportBatch, second["id"])).idempotency_key != original
                assert (await db.get(ExportBatch, first["id"])).idempotency_key == original
        finally:
            await engine.dispose()
    asyncio.run(run())
