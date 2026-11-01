"""Ambiguous processor references remain unmatched without quadratic candidate scans."""

import asyncio

from apps.ingest import reconciliation, service
from models import Base, CanonicalTransaction
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


def test_reconciliation_flags_all_transactions_when_a_reference_is_ambiguous():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                common = {
                    "event_time": "2026-10-01T10:00:00Z",
                    "store_id": "S1",
                    "payment_reference": "REUSED-REFERENCE",
                }
                await service.ingest(db, "POS", "pos-delivery", [
                    {**common, "source_record_id": "pos-1", "record_type": "Sale", "amount": "10", "register_id": "R1"},
                    {**common, "source_record_id": "pos-2", "record_type": "Sale", "amount": "10", "register_id": "R2"},
                ])
                await service.ingest(db, "Processor", "processor-delivery", [
                    {**common, "source_record_id": "processor-1", "record_type": "Capture", "amount": "10"},
                    {**common, "source_record_id": "processor-2", "record_type": "Capture", "amount": "10"},
                ])
                await db.commit()

                result = await reconciliation.reconcile(db, "2026-10-01")
                statuses = (await db.execute(select(CanonicalTransaction.reconciliation_status))).scalars().all()

                assert result["matched_pairs"] == 0
                assert result["exceptions"]["UNMATCHED_SALE"] == 2
                assert result["exceptions"]["ORPHAN_PAYMENT"] == 2
                assert statuses == ["Exception"] * 4
        finally:
            await engine.dispose()

    asyncio.run(run())
