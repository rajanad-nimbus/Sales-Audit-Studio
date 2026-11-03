"""The over/short report sums short and over separately, by store, register, cashier or day."""

import asyncio
from decimal import Decimal

from apps.cash.routes import over_short_report
from models import Base, CashControl
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

FINANCE = {"user": "finance", "role": "finance"}


def declaration(index, store, day, register, cashier, variance, tender="Cash"):
    return CashControl(
        id=f"c{index}",
        store_id=store,
        business_date=day,
        register_id=register,
        cashier_id=cashier,
        tender_type=tender,
        declared_cash=Decimal("0"),
        variance_amount=Decimal(variance),
        declared_by="x",
    )


def run_with_db(scenario):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add_all(
                    [
                        declaration(1, "S1", "2026-10-01", "R1", "A", "5"),  # short 5
                        declaration(2, "S1", "2026-10-01", "R2", "B", "-3"),  # over 3
                        declaration(3, "S1", "2026-10-02", "R1", "A", "0"),  # balanced
                        declaration(4, "S2", "2026-10-01", "R1", "C", "-10"),  # over 10
                        declaration(5, "S1", "2026-10-01", "R1", "A", "99", tender="Card"),
                    ]
                )
                await db.commit()
                await scenario(db)
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_short_and_over_do_not_offset_each_other():
    async def scenario(db):
        report = await over_short_report("store", None, None, None, db, FINANCE)
        by_store = {row["store_id"]: row for row in report["rows"]}
        assert by_store["S1"]["short"] == 5 and by_store["S1"]["over"] == 3
        assert by_store["S1"]["declarations"] == 3 and by_store["S1"]["out_of_balance"] == 2
        assert by_store["S1"]["net_short"] == 2
        assert by_store["S2"]["over"] == 10 and by_store["S2"]["short"] == 0
        # Card declarations are not cash over/short, so the 99 is left out.
        assert report["totals"] == {"declarations": 4, "short": 5, "over": 13}
        assert report["rows"][0]["store_id"] == "S2"  # largest absolute variance first

    run_with_db(scenario)


def test_grouping_date_range_and_store_scope():
    async def scenario(db):
        by_register = await over_short_report("register", None, None, None, db, FINANCE)
        assert {(r["store_id"], r["register_id"]) for r in by_register["rows"]} == {
            ("S1", "R1"),
            ("S1", "R2"),
            ("S2", "R1"),
        }
        by_day = await over_short_report("date", "2026-10-02", None, None, db, FINANCE)
        assert [r["business_date"] for r in by_day["rows"]] == ["2026-10-02"]
        manager = {"user": "m", "role": "store_manager", "store_scoped": True, "stores": ["S2"]}
        scoped = await over_short_report("cashier", None, None, None, db, manager)
        assert [(r["store_id"], r["cashier_id"]) for r in scoped["rows"]] == [("S2", "C")]

    run_with_db(scenario)
