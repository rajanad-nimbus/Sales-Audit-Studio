import asyncio

import pytest
from apps.ingest import service
from models import Base, SourceRecord
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


@pytest.mark.parametrize("field", ["unit_price", "gross_amount", "tax_amount"])
def test_subcent_transaction_detail_is_quarantined_without_rounding(field):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            record = {
                "source_record_id": f"subcent-{field}", "event_time": "2026-10-01T10:00:00Z",
                "record_type": "Sale", "amount": "10.00", "store_id": "S1",
                "lines": [{"item_code": "SKU-1", "quantity": "1", "unit_price": "10.00",
                           "gross_amount": "10.00", "tax_amount": "0.00"}],
                "tenders": [{"tender_type": "Card", "amount": "10.00"}],
            }
            record["lines"][0][field] = "10.005"
            async with session_factory() as db:
                result = await service.ingest(db, "POS", "subcent-detail", [record])
                stored = (await db.execute(select(SourceRecord))).scalar_one()
                assert result["quarantined"] == 1
                assert stored.status == "Quarantined"
                assert "two decimal places" in stored.quarantine_reason
        finally:
            await engine.dispose()

    asyncio.run(run())
