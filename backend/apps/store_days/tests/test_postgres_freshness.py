"""PostgreSQL concurrency checks; set ZETSA_TEST_POSTGRES_URL to a disposable database."""
import asyncio
import os
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.ingest.service import ingest
from apps.ingest.reconciliation import lock_reconciliation
from apps.store_days.freshness import lock_configuration, lock_store_day
from apps.store_days.routes import close_store_day, open_store_day_record, retotal_store_day
from apps.controls.rules.routes import RuleBody, create_rule
from apps.totals.routes import TotalDefinitionBody, create_definition
from models import (
    AuditTotal,
    AuditTotalDefinition,
    Base,
    FoundationReference,
    StoreDay,
    StoreDayClosureRequest,
)


POSTGRES_URL = os.getenv("ZETSA_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="ZETSA_TEST_POSTGRES_URL not configured")


def test_ingestion_and_retotal_serialize_in_both_orders():
    async def run():
        schema = "zetsa_test_" + uuid.uuid4().hex
        admin = create_async_engine(POSTGRES_URL)
        engine = None
        try:
            async with admin.begin() as connection:
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            engine = create_async_engine(POSTGRES_URL, connect_args={"server_settings": {"search_path": schema}})
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with sessions() as db:
                db.add_all([StoreDay(id="first", store_id="S1", business_date="2026-10-01"),
                            StoreDay(id="second", store_id="S2", business_date="2026-10-01"),
                            StoreDay(id="third", store_id="S3", business_date="2026-10-01")])
                await db.commit()
            record = {"source_record_id": "one", "record_type": "Sale", "store_id": "S1",
                      "event_time": "2026-10-01T10:00:00Z", "amount": "10"}
            async with sessions() as totaling, sessions() as incoming:
                await lock_store_day(totaling, "S1", "2026-10-01")
                task = asyncio.create_task(ingest(incoming, "POS", "one", [record]))
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(asyncio.shield(task), 0.2)
                await retotal_store_day("S1", "2026-10-01", totaling, {"user": "finance"})
                await task
                await incoming.commit()
            async with sessions() as db:
                assert (await db.get(StoreDay, "first")).totals_stale
            async with sessions() as incoming, sessions() as totaling:
                await ingest(incoming, "POS", "two", [{**record, "source_record_id": "two", "store_id": "S2"}])
                task = asyncio.create_task(retotal_store_day("S2", "2026-10-01", totaling, {"user": "finance"}))
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(asyncio.shield(task), 0.2)
                await incoming.commit()
                await task
            async with sessions() as db:
                assert not (await db.get(StoreDay, "second")).totals_stale
                total = (await db.execute(select(AuditTotal).where(
                    AuditTotal.store_day_id == "second", AuditTotal.total_name == "POS Net",
                    AuditTotal.level == "Store"))).scalar_one()
                assert total.calculated_amount == 10
            async with sessions() as totaling, sessions() as configuring:
                await lock_configuration(totaling)
                await lock_store_day(totaling, "S3", "2026-10-01")
                task = asyncio.create_task(create_definition(TotalDefinitionBody(name="New control"), configuring,
                                                             {"user": "finance"}))
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(asyncio.shield(task), 0.2)
                await retotal_store_day("S3", "2026-10-01", totaling, {"user": "finance"})
                await task
            async with sessions() as db:
                day = await db.get(StoreDay, "third")
                assert day.audit_version == 1 and day.totals_stale
                db.add(FoundationReference(id="store4", category="Store", code="S4", name="Store 4", active=True))
                await db.commit()
            async with sessions() as configuring, sessions() as opening:
                await lock_configuration(configuring, exclusive=True)
                task = asyncio.create_task(open_store_day_record(opening, "S4", "2026-10-01", "finance"))
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(asyncio.shield(task), 0.2)
                await create_definition(TotalDefinitionBody(name="Later control"), configuring, {"user": "finance"})
                assert (await task).store_id == "S4"
                await opening.commit()

            async with sessions() as db:
                db.add(AuditTotalDefinition(id="count-total", name="Count total", aggregation="Count", active=True))
                await db.commit()
            async with sessions() as configuring, sessions() as rule_writer:
                await lock_configuration(configuring, exclusive=True)
                task = asyncio.create_task(create_rule(
                    RuleBody(name="Count threshold", total_name="Configured: Count total", threshold=0,
                             threshold_currency=None),
                    rule_writer,
                    {"user": "finance"},
                ))
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(asyncio.shield(task), 0.2)
                count_total = await configuring.get(AuditTotalDefinition, "count-total")
                count_total.aggregation = "Sum"
                await configuring.commit()
                with pytest.raises(HTTPException) as exc:
                    await task
                assert exc.value.status_code == 422
        finally:
            if engine:
                await engine.dispose()
            async with admin.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await admin.dispose()
    asyncio.run(run())


def test_store_day_close_waits_for_reconciliation_lock():
    async def run():
        schema = "zetsa_test_" + uuid.uuid4().hex
        admin = create_async_engine(POSTGRES_URL)
        engine = None
        try:
            async with admin.begin() as connection:
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            engine = create_async_engine(POSTGRES_URL, connect_args={"server_settings": {"search_path": schema}})
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with sessions() as db:
                db.add(StoreDay(
                    id="close-race",
                    store_id="S1",
                    business_date="2026-10-01",
                    status="Auditing",
                    audit_version=1,
                    totals_stale=False,
                ))
                db.add_all([
                    AuditTotal(
                        id="processor-bank",
                        store_day_id="close-race",
                        audit_version=1,
                        total_name="Processor to Bank Variance",
                        level="Store",
                        calculated_amount=0,
                        declared_amount=0,
                        variance_amount=0,
                        currency="USD",
                    ),
                    AuditTotal(
                        id="pos-gl",
                        store_day_id="close-race",
                        audit_version=1,
                        total_name="POS to GL Variance",
                        level="Store",
                        calculated_amount=0,
                        declared_amount=0,
                        variance_amount=0,
                        currency="USD",
                    ),
                    StoreDayClosureRequest(
                        id="closure-request",
                        store_day_id="close-race",
                        requested_by="finance",
                        evidence_basis="Reviewed reconciliation totals",
                        status="Pending",
                    ),
                ])
                await db.commit()

            async with sessions() as reconciliation_owner, sessions() as closer:
                await lock_reconciliation(reconciliation_owner)
                task = asyncio.create_task(
                    close_store_day(
                        "S1",
                        "2026-10-01",
                        closer,
                        {"user": "finance", "role": "finance"},
                    )
                )
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(asyncio.shield(task), 0.2)
                await reconciliation_owner.commit()
                result = await task
                assert result["status"] == "Closed"
        finally:
            if engine:
                await engine.dispose()
            async with admin.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await admin.dispose()

    asyncio.run(run())
