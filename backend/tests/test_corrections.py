"""Governed correction controls."""
import asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from models import Base, CanonicalTransaction, SourceRecord, TransactionAdjustment
from routes_corrections import approve_adjustment


def test_approved_missing_transaction_creates_immutable_lineage():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(engine, expire_on_commit=False)
        async with Session() as db:
            db.add(TransactionAdjustment(
                id="adjustment-1", transaction_id=None, adjustment_type="MissingTransaction",
                proposed_values={"business_date": "2026-10-06", "store_id": "S1", "amount": "12.50", "transaction_type": "Sale", "tender_type": "Cash"},
                rationale="Verified receipt", status="Proposed", proposed_by="analyst", reviewed_by=None, reviewed_at=None,
            ))
            await db.commit()
            result = await approve_adjustment("adjustment-1", db, {"user": "finance.user"})
            transaction = (await db.execute(select(CanonicalTransaction))).scalar_one()
            source = (await db.execute(select(SourceRecord))).scalar_one()
            assert result["status"] == "Approved"
            assert result["transaction_id"] == transaction.id
            assert str(transaction.signed_amount) == "12.50"
            assert transaction.source_record_id == source.id
            assert source.source_system == "ManualAdjustment"
        await engine.dispose()
    asyncio.run(run())
