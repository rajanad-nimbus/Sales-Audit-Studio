import asyncio

from apps.corrections.routes import approve_adjustment
from apps.ingest import reconciliation, service
from models import Base, CanonicalTransaction, TransactionAdjustment
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


def test_reconciliation_uses_approved_processor_amount():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                common = {
                    "event_time": "2026-10-01T10:00:00Z",
                    "store_id": "S1",
                    "payment_reference": "REF",
                }
                await service.ingest(
                    db,
                    "POS",
                    "pos",
                    [{**common, "source_record_id": "pos", "record_type": "Sale", "amount": "100"}],
                )
                await service.ingest(
                    db,
                    "Processor",
                    "processor",
                    [
                        {
                            **common,
                            "source_record_id": "proc",
                            "record_type": "Capture",
                            "amount": "90",
                        }
                    ],
                )
                processor = (
                    await db.execute(
                        select(CanonicalTransaction).where(
                            CanonicalTransaction.source_lineage == "processor-map-v1"
                        )
                    )
                ).scalar_one()
                db.add(
                    TransactionAdjustment(
                        id="adjustment",
                        transaction_id=processor.id,
                        adjustment_type="Amount",
                        proposed_values={"amount_delta": "10"},
                        status="Proposed",
                        rationale="verified",
                        proposed_by="analyst",
                    )
                )
                await db.commit()
                await approve_adjustment("adjustment", db, {"user": "finance"})
                result = await reconciliation.reconcile(db, "2026-10-01")
                assert (
                    result["matched_pairs"] == 1 and "AMOUNT_MISMATCH" not in result["exceptions"]
                )
                assert (
                    processor.signed_amount == 90 and processor.reconciliation_status == "Matched"
                )
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_shopify_refund_comparison_uses_approved_correction():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                common = {
                    "event_time": "2026-10-01T10:00:00Z",
                    "store_id": "S1",
                    "payment_reference": "ORDER",
                }
                await service.ingest(
                    db,
                    "Shopify",
                    "delivery",
                    [
                        {
                            **common,
                            "source_record_id": "order",
                            "record_type": "Order",
                            "amount": "100",
                            "financial_status": "paid",
                        },
                        {
                            **common,
                            "source_record_id": "refund",
                            "record_type": "Refund",
                            "amount": "90",
                            "order_id": "ORDER",
                        },
                    ],
                )
                refund = (
                    await db.execute(
                        select(CanonicalTransaction).where(
                            CanonicalTransaction.transaction_type == "Refund"
                        )
                    )
                ).scalar_one()
                db.add(
                    TransactionAdjustment(
                        id="adjustment",
                        transaction_id=refund.id,
                        adjustment_type="Amount",
                        proposed_values={"amount_delta": "-10"},
                        status="Proposed",
                        rationale="verified",
                        proposed_by="analyst",
                    )
                )
                await db.commit()
                await approve_adjustment("adjustment", db, {"user": "finance"})
                result = await reconciliation.reconcile(db, "2026-10-01")
                assert "SHOPIFY_PARTIAL_REFUND" not in result["exceptions"]
                assert refund.signed_amount == -90 and refund.reconciliation_status == "Matched"
        finally:
            await engine.dispose()

    asyncio.run(run())
