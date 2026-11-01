import asyncio

import pytest
from apps.ingest import service
from models import Base, SourceRecord
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "-Infinity", "10000000000000", "10.005"])
@pytest.mark.parametrize("source", ["POS", "Shopify", "POSControl"])
def test_invalid_amount_is_quarantined(source, amount):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            session = async_sessionmaker(engine, expire_on_commit=False)
            record = {
                "source_record_id": "1",
                "event_time": "2026-10-01T10:00:00Z",
                "record_type": "Order" if source == "Shopify" else "Sale",
                "amount": amount,
                "store_id": "S1",
                "payment_reference": "P1",
                "business_date": "2026-10-01",
                "declared_count": 1,
                "declared_total": amount,
            }
            async with session() as db:
                stats = await service.ingest(db, source, "d1", [record])
                assert stats["quarantined"] == 1
                stored = (await db.execute(select(SourceRecord))).scalar_one()
                assert stored.status == "Quarantined"
                assert stored.quarantine_reason
        finally:
            await engine.dispose()

    asyncio.run(run())
