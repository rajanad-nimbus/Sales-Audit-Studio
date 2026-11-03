"""Transaction numbers missing from a register's sequence are flagged once per store day."""

import asyncio
from datetime import datetime, timedelta

from apps.ingest.reconciliation import reconcile
from apps.ingest.sequence import find_gaps, missing_numbers, parse
from apps.ingest.service import ingest
from apps.store_days.routes import store_day_sequence_gaps
from models import Base, Case, FoundationReference, RegisterEvent, StoreDay
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

DAY = "2026-10-01"
USER = {"user": "analyst", "role": "analyst"}


def run_with_db(scenario):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                await scenario(db)
        finally:
            await engine.dispose()

    asyncio.run(run())


def sale(number: int, register: str = "R1") -> dict:
    return {
        "source_record_id": f"t-{register}-{number}",
        "record_type": "Sale",
        "event_time": f"{DAY}T10:00:00Z",
        "store_id": "S1",
        "register_id": register,
        "transaction_number": str(number),
        "amount": "5",
    }


def test_gaps_are_the_numbers_absent_between_first_and_last():
    assert missing_numbers({1, 2, 5}) == [3, 4]
    assert missing_numbers({4}) == []
    assert missing_numbers({1, 2, 3}) == []
    assert missing_numbers({1, 500_000}) == []  # a numbering reset, not 500,000 lost sales
    assert parse("0") is None and parse("00017") == 17 and parse("A12") is None


def test_register_events_fill_the_sequence_and_each_register_is_separate():
    async def scenario(db):
        db.add_all(
            [
                FoundationReference(id="r", category="Store", code="S1", name="S1", active=True),
                StoreDay(id="d", store_id="S1", business_date=DAY),
            ]
        )
        await db.flush()
        await ingest(db, "POS", "d1", [sale(1), sale(2), sale(5), sale(1, "R2"), sale(2, "R2")])
        db.add(
            RegisterEvent(
                id="ev",
                source_record_id="ev",
                source_format="x",
                store_id="S1",
                business_date=DAY,
                register_id="R1",
                event_time=datetime.fromisoformat(f"{DAY}T10:30:00+00:00"),
                event_type="NoSale",
                transaction_number="3",
            )
        )
        await db.commit()

        assert await find_gaps(db, DAY) == {("S1", DAY): {"R1": [4]}}
        detail = await store_day_sequence_gaps("S1", DAY, db, USER)
        assert detail["registers"] == [{"register_id": "R1", "missing": [4], "count": 1}]

    run_with_db(scenario)


def test_reconciliation_raises_one_case_and_not_again_while_it_is_open():
    async def scenario(db):
        await ingest(db, "POS", "d1", [sale(1), sale(2), sale(5)])
        await db.commit()

        first = await reconcile(db, DAY)
        assert first["exceptions"].get("MISSING_TRAN") == 1
        await db.commit()
        second = await reconcile(db, DAY)
        assert "MISSING_TRAN" not in second["exceptions"]
        cases = (
            (await db.execute(select(Case).where(Case.case_type == "Missing Transaction Number")))
            .scalars()
            .all()
        )
        assert [(c.store_id, c.business_date) for c in cases] == [("S1", DAY)]

        # Once the gap is filled and the case closed, nothing is raised.
        cases[0].status = "Closed"
        await ingest(db, "POS", "d2", [sale(3), sale(4)])
        await db.commit()
        third = await reconcile(db, DAY)
        assert "MISSING_TRAN" not in third["exceptions"]
        assert timedelta(0) == timedelta(0)

    run_with_db(scenario)
