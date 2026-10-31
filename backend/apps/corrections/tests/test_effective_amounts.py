import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from fastapi import HTTPException
import pytest

from apps.corrections.routes import approve_adjustment, validate_missing_transaction
from apps.ingest.service import ingest
from apps.store_days.routes import retotal_store_day
from apps.totals.routes import preview_definition
from models import AuditTotal, AuditTotalDefinition, Base, CanonicalTransaction, StoreDay, TransactionAdjustment


def test_retotal_and_preview_apply_approved_corrections_in_order_by_source():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add_all([StoreDay(id="day", store_id="S1", business_date="2026-10-01"),
                    AuditTotalDefinition(id="definition", name="POS Sales", source_system="POS", level="Register", active=True)])
                common = {"event_time": "2026-10-01T10:00:00Z", "store_id": "S1", "amount": "100"}
                await ingest(db, "POS", "one", [{**common, "source_record_id": "pos", "record_type": "Sale", "register_id": "R1"}])
                await ingest(db, "Processor", "two", [{**common, "source_record_id": "processor", "record_type": "Capture"}])
                transactions = (await db.execute(select(CanonicalTransaction))).scalars().all()
                pos = next(row for row in transactions if row.source_lineage == "pos-map-v1")
                processor = next(row for row in transactions if row.source_lineage == "processor-map-v1")
                when = datetime(2026, 10, 2, tzinfo=timezone.utc)
                for identifier, transaction, values, minutes in [
                    ("a", pos, {"amount_delta": "5"}, 1), ("b", pos, {"corrected_amount": "110"}, 2),
                    ("c", pos, {"amount_delta": "2"}, 3), ("d", processor, {"amount_delta": "-3"}, 4),
                ]:
                    db.add(TransactionAdjustment(id=identifier, transaction_id=transaction.id, adjustment_type="Amount",
                        proposed_values=values, status="Approved", rationale="test", proposed_by="analyst",
                        reviewed_at=when + timedelta(minutes=minutes)))
                db.add(TransactionAdjustment(id="unapproved", transaction_id=pos.id, adjustment_type="Amount",
                    proposed_values={"amount_delta": "999"}, status="Proposed", rationale="test", proposed_by="analyst"))
                await db.commit()
                preview = await preview_definition("definition", "S1", "2026-10-01", db, {"user": "finance"})
                assert preview["value"] == Decimal("112")
                await retotal_store_day("S1", "2026-10-01", db, {"user": "finance"})
                totals = (await db.execute(select(AuditTotal))).scalars().all()
                values = {(item.total_name, item.level, item.dimension_key): item.calculated_amount for item in totals}
                assert values[("POS Net", "Store", None)] == 112
                assert values[("POS Net", "Register", "R1")] == 112
                assert values[("Processor Net", "Store", None)] == 97
                assert values[("Approved Adjustment Net", "Store", None)] == 9
                assert values[("Configured: POS Sales", "Register", "R1")] == preview["value"]
                assert pos.signed_amount == 100 and processor.signed_amount == 100
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_approval_rejects_a_delta_that_would_overflow_the_effective_amount():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                await ingest(db, "POS", "delivery", [{"source_record_id": "s", "record_type": "Sale",
                    "event_time": "2026-10-01T10:00:00Z", "store_id": "S1", "amount": "9999999999999.99"}])
                transaction = (await db.execute(select(CanonicalTransaction))).scalar_one()
                db.add(TransactionAdjustment(id="adjustment", transaction_id=transaction.id, adjustment_type="Amount",
                    proposed_values={"amount_delta": "1"}, status="Proposed", rationale="test", proposed_by="analyst"))
                await db.commit()
                with pytest.raises(HTTPException, match="exceeds supported"):
                    await approve_adjustment("adjustment", db, {"user": "finance"})
                assert (await db.get(TransactionAdjustment, "adjustment")).status == "Proposed"
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_ambiguous_refunds_require_a_compatible_source():
    values = {"business_date": "2026-10-01", "store_id": "S1", "amount": "5", "transaction_type": "Refund"}
    with pytest.raises(HTTPException, match="source_system"):
        validate_missing_transaction(values)
    validate_missing_transaction({**values, "source_system": "Shopify"})
    validate_missing_transaction({**values, "source_system": "Processor"})
    with pytest.raises(HTTPException, match="source_system"):
        validate_missing_transaction({**values, "source_system": "POS"})
