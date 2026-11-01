import asyncio

import pytest
from fastapi import HTTPException
from models import Base, CanonicalTransaction, TransactionAdjustment
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.corrections.routes import approve_adjustment
from apps.ingest.service import ingest


@pytest.mark.parametrize(
    "record_type,amount_delta",
    [("Sale", "-101"), ("Return", "101")],
)
def test_amount_correction_cannot_reverse_transaction_sign(record_type, amount_delta):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as db:
                await ingest(
                    db,
                    "POS",
                    "delivery",
                    [
                        {
                            "source_record_id": f"source-{record_type}",
                            "record_type": record_type,
                            "event_time": "2026-10-01T10:00:00Z",
                            "amount": "100",
                            "store_id": "S1",
                        }
                    ],
                )
                transaction = (await db.execute(select_transaction())).scalar_one()
                db.add(
                    TransactionAdjustment(
                        id="adjustment",
                        transaction_id=transaction.id,
                        adjustment_type="Amount",
                        proposed_values={"amount_delta": amount_delta},
                        status="Proposed",
                        rationale="Correct the source amount",
                        proposed_by="analyst",
                    )
                )
                await db.commit()

                with pytest.raises(HTTPException, match="cannot reverse") as error:
                    await approve_adjustment("adjustment", db, {"user": "finance"})

                assert error.value.status_code == 422
                adjustment = await db.get(TransactionAdjustment, "adjustment")
                assert adjustment.status == "Proposed"
        finally:
            await engine.dispose()

    asyncio.run(run())


def select_transaction():
    from sqlalchemy import select

    return select(CanonicalTransaction)
