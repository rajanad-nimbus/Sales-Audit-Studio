import asyncio

import pytest
from apps.ingest.service import ingest
from apps.store_days.routes import retotal_store_day
from apps.totals.routes import preview_definition
from fastapi import HTTPException
from models import AuditTotal, AuditTotalDefinition, Base, StoreDay
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


def test_retotal_and_preview_preserve_currency_and_reject_mixed_sources():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                day = StoreDay(id="day", store_id="S1", business_date="2026-10-01")
                db.add_all([day, AuditTotalDefinition(id="total", name="Sales", source_system="POS", active=True)])
                record = {"source_record_id": "sale", "record_type": "Sale", "store_id": "S1",
                          "event_time": "2026-10-01T10:00:00Z", "amount": "10", "currency": "EUR"}
                await ingest(db, "POS", "first", [record])
                await db.commit()
                assert (await preview_definition("total", "S1", "2026-10-01", db, {}))["currency"] == "EUR"
                await retotal_store_day("S1", "2026-10-01", db, {"user": "finance"})
                assert {total.currency for total in (await db.execute(select(AuditTotal))).scalars()} == {"EUR"}
                await ingest(db, "Bank", "second", [{**record, "source_record_id": "bank", "record_type": "Deposit",
                                                       "currency": "USD"}])
                await db.commit()
                with pytest.raises(HTTPException, match="multiple currencies"):
                    await retotal_store_day("S1", "2026-10-01", db, {"user": "finance"})
                with pytest.raises(HTTPException, match="multiple currencies"):
                    await preview_definition("total", "S1", "2026-10-01", db, {})
                assert day.audit_version == 1
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_manifest_currency_must_match_and_invalid_codes_are_quarantined():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add(StoreDay(id="day", store_id="S1", business_date="2026-10-01"))
                record = {"source_record_id": "sale", "record_type": "Sale", "store_id": "S1",
                          "event_time": "2026-10-01T10:00:00Z", "amount": "10", "currency": "EUR"}
                result = await ingest(db, "POS", "first", [record, {**record, "source_record_id": "bad", "currency": "eur"}])
                assert result["quarantined"] == 1
                await ingest(db, "POSControl", "manifest", [{"source_record_id": "manifest", "store_id": "S1",
                    "business_date": "2026-10-01", "declared_count": 1, "declared_total": "10", "currency": "USD"}])
                await db.commit()
                with pytest.raises(HTTPException, match="multiple currencies"):
                    await retotal_store_day("S1", "2026-10-01", db, {"user": "finance"})
                assert not (await db.execute(select(AuditTotal))).scalars().all()
        finally:
            await engine.dispose()
    asyncio.run(run())
