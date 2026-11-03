"""Over/Short totals compare what a store was accountable for with what it accounted for."""

import asyncio
from decimal import Decimal

from models import AuditTotal, AuditTotalDefinition, Base, StoreDay
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.ingest.service import ingest
from apps.store_days import routes
from apps.totals.over_short import OverShort, calculate

FINANCE = {"user": "finance", "role": "finance"}


def member(identifier, group, operator="+"):
    return AuditTotalDefinition(
        id=identifier,
        name=identifier,
        category="Over/Short",
        over_short_group=group,
        over_short_operator=operator,
    )


def test_operators_add_to_or_take_from_their_group_and_short_is_positive():
    definitions = [
        member("sales", "Accountable For"),
        member("returns", "Accountable For", "-"),
        member("cash", "Accounted For"),
        AuditTotalDefinition(id="other", name="other", category="Miscellaneous"),
    ]
    totals = {
        "sales": {None: Decimal("100")},
        "returns": {None: Decimal("10")},
        "cash": {"R1": Decimal("60"), "R2": Decimal("25")},
        "other": {None: Decimal("999")},
    }
    result = calculate(definitions, totals)
    assert result == OverShort(Decimal("90"), Decimal("85"))
    assert result.variance == Decimal("5")  # short
    assert calculate([definitions[3]], totals) is None


def test_retotal_records_an_over_short_total_for_the_store_day():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add(StoreDay(id="day", store_id="S1", business_date="2026-10-01"))
                db.add_all(
                    [
                        AuditTotalDefinition(
                            id="sales",
                            name="All sales",
                            category="Over/Short",
                            over_short_group="Accountable For",
                            over_short_operator="+",
                            source_system="POS",
                            transaction_types=["Sale"],
                        ),
                        AuditTotalDefinition(
                            id="cash",
                            name="Cash taken",
                            category="Over/Short",
                            over_short_group="Accounted For",
                            over_short_operator="+",
                            source_system="POS",
                            transaction_types=["Sale"],
                            tender_type="Cash",
                        ),
                    ]
                )
                common = {"event_time": "2026-10-01T10:00:00Z", "store_id": "S1"}
                await ingest(
                    db,
                    "POS",
                    "d1",
                    [
                        {
                            **common,
                            "source_record_id": "a",
                            "record_type": "Sale",
                            "amount": "50",
                            "tender_type": "Cash",
                        },
                        {
                            **common,
                            "source_record_id": "b",
                            "record_type": "Sale",
                            "amount": "30",
                            "tender_type": "Card",
                        },
                    ],
                )
                await db.commit()
                await routes.retotal_store_day("S1", "2026-10-01", db, FINANCE)
                total = (
                    await db.execute(
                        select(AuditTotal).where(AuditTotal.total_name == "Over/Short")
                    )
                ).scalar_one()
                assert (total.calculated_amount, total.declared_amount) == (80, 50)
                assert total.variance_amount == 30  # 30 of sales are not accounted for in cash
        finally:
            await engine.dispose()

    asyncio.run(run())
