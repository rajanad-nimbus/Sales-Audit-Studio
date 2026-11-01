import asyncio

import pytest
from apps.ingest import reconciliation, service
from models import Base, CanonicalTransaction, SourceRecord
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


@pytest.mark.parametrize("settled_on", ["not-a-date", "2026-02-30", "2026-10-01junk"])
def test_invalid_settlement_date_does_not_break_delivery_or_reconciliation(settled_on):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session = async_sessionmaker(engine, expire_on_commit=False)
            common = {"event_time": "2026-10-01T10:00:00Z", "record_type": "Capture",
                      "amount": "10", "store_id": "S1"}
            async with session() as db:
                stats = await service.ingest(db, "Processor", "d1", [
                    {**common, "source_record_id": "bad", "settled_on": settled_on},
                    {**common, "source_record_id": "good", "settled_on": "2026-10-01T12:00:00Z"},
                ])
                assert stats["quarantined"] == 1
                assert stats["canonical_created"] == 1
                records = (await db.execute(select(SourceRecord).order_by(SourceRecord.source_record_id))).scalars().all()
                assert records[0].quarantine_reason == "settled_on is not ISO-8601"
                transaction = (await db.execute(select(CanonicalTransaction))).scalar_one()
                assert transaction.settlement_date == "2026-10-01"
                result = await reconciliation.reconcile(db, "2026-10-01")
                assert result["transactions_examined"] == 1
        finally:
            await engine.dispose()

    asyncio.run(run())
