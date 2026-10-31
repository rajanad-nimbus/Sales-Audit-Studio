import asyncio

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.ingest.service import ingest
from apps.corrections.routes import approve_adjustment
from apps.store_days import routes
from models import AuditTotal, Base, StoreDay, StoreDayClosureRequest, TransactionAdjustment


def test_late_delivery_invalidates_totals_and_pending_closure_until_retotal():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                day = StoreDay(id="day", store_id="S1", business_date="2026-10-01")
                db.add(day)
                record = {"source_record_id": "sale", "record_type": "Sale", "store_id": "S1",
                          "event_time": "2026-10-01T10:00:00Z", "amount": "10"}
                await ingest(db, "POS", "first", [record])
                await db.commit()
                user = {"user": "finance"}
                await routes.retotal_store_day("S1", "2026-10-01", db, user)
                db.add(StoreDayClosureRequest(id="req", store_day_id="day", requested_by="reviewer", evidence_basis="verified"))
                await db.commit()
                await ingest(db, "POS", "duplicate", [record])
                assert not day.totals_stale
                assert (await db.get(StoreDayClosureRequest, "req")).status == "Pending"
                await ingest(db, "POS", "late", [{**record, "source_record_id": "late"}])
                await db.commit()
                assert day.totals_stale
                assert (await db.get(StoreDayClosureRequest, "req")).status == "Superseded"
                with pytest.raises(HTTPException, match="totals are stale"):
                    await routes.close_store_day("S1", "2026-10-01", db, user)
                await routes.retotal_store_day("S1", "2026-10-01", db, user)
                assert not day.totals_stale
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_late_manifest_marks_closed_day_stale():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                day = StoreDay(id="day", store_id="S1", business_date="2026-10-01", status="Closed", audit_version=1)
                db.add(day)
                await db.commit()
                await ingest(db, "POSControl", "late", [{"source_record_id": "manifest", "store_id": "S1",
                    "business_date": "2026-10-01", "declared_count": 0, "declared_total": "0"}])
                await db.commit()
                assert day.status == "Closed" and day.totals_stale
                assert routes.store_day_view(day)["totals_stale"]
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_approving_a_missing_transaction_invalidates_existing_totals():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                day = StoreDay(id="day", store_id="S1", business_date="2026-10-01")
                db.add(day)
                await db.commit()
                await routes.retotal_store_day("S1", "2026-10-01", db, {"user": "finance"})
                db.add(TransactionAdjustment(id="adjustment", adjustment_type="MissingTransaction", status="Proposed",
                    proposed_values={"business_date": "2026-10-01", "store_id": "S1", "amount": "5",
                                     "transaction_type": "Sale"}, rationale="verified", proposed_by="analyst"))
                await db.commit()
                await approve_adjustment("adjustment", db, {"user": "finance"})
                assert day.totals_stale
                await routes.retotal_store_day("S1", "2026-10-01", db, {"user": "finance"})
                total = (await db.execute(select(AuditTotal).where(AuditTotal.store_day_id == "day",
                    AuditTotal.audit_version == 2, AuditTotal.total_name == "POS Net", AuditTotal.level == "Store"))).scalar_one()
                assert total.calculated_amount == 5
        finally:
            await engine.dispose()
    asyncio.run(run())
