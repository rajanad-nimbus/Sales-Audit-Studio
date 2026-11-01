import asyncio

from apps.ingest import reconciliation, service
from apps.ingest.routes import run_reconciliation
from models import Base, CanonicalTransaction, Case, SourceRecord
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


def test_store_scoped_reconciliation_only_changes_assigned_store_data():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                day = "2026-10-01"
                for store in ("S1", "S2"):
                    await service.ingest(
                        db,
                        "POS",
                        f"pos-{store}",
                        [
                            {
                                "source_record_id": f"sale-{store}",
                                "event_time": f"{day}T10:00:00Z",
                                "record_type": "Sale",
                                "amount": "10.00",
                                "store_id": store,
                                "payment_reference": f"payment-{store}",
                            }
                        ],
                    )
                    await service.ingest(
                        db,
                        "POSControl",
                        f"control-{store}",
                        [
                            {
                                "source_record_id": f"control-{store}",
                                "business_date": day,
                                "store_id": store,
                                "declared_count": 1,
                                "declared_total": "10.00",
                            }
                        ],
                    )

                result = await run_reconciliation(
                    day,
                    db,
                    {"user": "finance", "role": "finance", "store_scoped": True, "stores": ["S1"]},
                )

                assert result["transactions_examined"] == 1
                transactions = (await db.execute(select(CanonicalTransaction))).scalars().all()
                assert {row.store_id: row.reconciliation_status for row in transactions} == {
                    "S1": "Exception",
                    "S2": "Unmatched",
                }
                manifests = (
                    (
                        await db.execute(
                            select(SourceRecord).where(SourceRecord.source_system == "POSControl")
                        )
                    )
                    .scalars()
                    .all()
                )
                assert {row.source_record_id: row.status for row in manifests} == {
                    "control-S1": "Processed",
                    "control-S2": "Archived",
                }
                cases = (await db.execute(select(Case))).scalars().all()
                assert len(cases) == 1 and cases[0].store_id == "S1"
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_reconciliation_with_empty_store_scope_is_fail_closed():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                day = "2026-10-01"
                await service.ingest(
                    db,
                    "POS",
                    "pos-S1",
                    [
                        {
                            "source_record_id": "sale-S1",
                            "event_time": f"{day}T10:00:00Z",
                            "record_type": "Sale",
                            "amount": "10.00",
                            "store_id": "S1",
                            "payment_reference": "payment-S1",
                        }
                    ],
                )
                await service.ingest(
                    db,
                    "POSControl",
                    "control-S1",
                    [
                        {
                            "source_record_id": "control-S1",
                            "business_date": day,
                            "store_id": "S1",
                            "declared_count": 1,
                            "declared_total": "10.00",
                        }
                    ],
                )

                result = await reconciliation.reconcile(db, day, store_ids=set())

                transaction = (await db.execute(select(CanonicalTransaction))).scalar_one()
                manifest = (
                    await db.execute(
                        select(SourceRecord).where(SourceRecord.source_system == "POSControl")
                    )
                ).scalar_one()
                assert result["transactions_examined"] == 0
                assert transaction.reconciliation_status == "Unmatched"
                assert manifest.status == "Archived"
                assert (await db.execute(select(Case))).scalars().all() == []
        finally:
            await engine.dispose()

    asyncio.run(run())
