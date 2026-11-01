import asyncio

import pytest
from apps.ingest import reconciliation, service
from models import Base, CanonicalTransaction
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


def test_reconciliation_refuses_oversized_scope_before_mutating_rows(monkeypatch):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as db:
                db.add_all([
                    CanonicalTransaction(
                        id=f"transaction-{index}", business_date="2026-10-01",
                        transaction_date=service.utcnow(), event_timestamp=service.utcnow(),
                        processing_timestamp=service.utcnow(), settlement_date="2026-10-01",
                        transaction_type="Sale", source_lineage="pos-map-v1", currency="USD",
                        signed_amount=100, store_id="S1", channel_id="store",
                        reconciliation_status="Unmatched", disposition="Open",
                    )
                    for index in range(3)
                ])
                await db.flush()
                monkeypatch.setattr(reconciliation, "MAX_RECONCILIATION_TRANSACTIONS", 2)

                with pytest.raises(reconciliation.ReconciliationCapacityError, match="2-transaction limit"):
                    await reconciliation.reconcile(db, "2026-10-01")

                statuses = (await db.execute(select(CanonicalTransaction.reconciliation_status))).scalars().all()
                assert statuses == ["Unmatched", "Unmatched", "Unmatched"]
        finally:
            await engine.dispose()

    asyncio.run(run())
