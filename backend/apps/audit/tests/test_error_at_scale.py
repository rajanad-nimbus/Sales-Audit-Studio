"""The Audit Errors page stays quick and complete when an error has been raised many times."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from models import Base
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.audit.error_routes import cases_for_error, overview
from apps.cases.models import Case
from apps.cases.models import Exception as ExceptionRow

USER = {"role": "analyst"}
TODAY = datetime.now(UTC).date()


def day(offset: int) -> str:
    return (TODAY - timedelta(days=offset)).isoformat()


def add_case(db, identifier, *, store="S1", status="Open", when=1, code="UNMATCHED_SALE", errors=1):
    db.add(
        Case(
            id=identifier,
            case_number=f"C-{identifier}",
            case_type="x",
            status=status,
            business_date=day(when),
            store_id=store,
            currency="USD",
            total_exception_amount=Decimal("10"),
        )
    )
    for n in range(errors):
        db.add(
            ExceptionRow(
                id=f"{identifier}-e{n}",
                case_id=identifier,
                exception_type=code,
                exception_family="F",
                source_system="x",
                detection_origin="x",
                detection_rule_id="x",
                exception_amount=Decimal("10"),
                estimated_exposure=Decimal("10"),
                severity="Low",
                confidence="High",
                close_impact="No",
            )
        )


def run_with_db(scenario):
    async def go():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                await scenario(db)
        finally:
            await engine.dispose()

    asyncio.run(go())


def by_code(result):
    return {item["code"]: item for item in result["items"]}


def test_raised_counts_cover_a_window_but_open_counts_cover_every_open_case():
    async def scenario(db):
        add_case(db, "recent-open", when=5)
        add_case(db, "recent-closed", status="Closed", when=10)
        add_case(db, "old-closed", status="Closed", when=400)
        add_case(db, "old-open", when=400)  # still waiting, however old
        await db.commit()

        default = by_code(await overview(db, USER))["UNMATCHED_SALE"]
        assert default["open_count"] == 2  # both open cases, including the 400-day-old one
        assert default["total_count"] == 2  # only the two raised inside the 90-day window

        year = by_code(await overview(db, USER, days=365))["UNMATCHED_SALE"]
        assert (year["open_count"], year["total_count"]) == (2, 2)
        everything = await overview(db, USER, days=0)
        assert by_code(everything)["UNMATCHED_SALE"]["total_count"] == 4
        assert (everything["window_days"], (await overview(db, USER))["window_days"]) == (0, 90)

    run_with_db(scenario)


def test_an_error_open_only_outside_the_window_is_not_reported_as_never_raised():
    async def scenario(db):
        add_case(db, "old", when=500, code="TAX_MISMATCH")
        await db.commit()
        item = by_code(await overview(db, USER, days=30))["TAX_MISMATCH"]
        assert (item["open_count"], item["total_count"]) == (1, 0)
        assert item["latest_at"] is not None  # it was raised, just not lately

    run_with_db(scenario)


def test_the_cases_for_an_error_come_a_page_at_a_time_with_nothing_repeated_or_skipped():
    async def scenario(db):
        for n in range(60):
            add_case(db, f"c{n:03d}", when=n % 30 + 1)
        await db.commit()

        pages = [
            await cases_for_error("UNMATCHED_SALE", 25, db, USER, page=number)
            for number in (1, 2, 3, 4)
        ]
        assert [len(p["items"]) for p in pages] == [25, 25, 10, 0]
        assert [p["has_more"] for p in pages] == [True, True, False, False]
        ids = [c["id"] for p in pages for c in p["items"]]
        assert len(ids) == len(set(ids)) == 60
        dates = [c["business_date"] for p in pages for c in p["items"]]
        assert dates == sorted(dates, reverse=True)  # newest business date first

    run_with_db(scenario)


def test_the_cases_for_an_error_can_be_narrowed_by_store_and_date():
    async def scenario(db):
        add_case(db, "a", store="S100", when=2)
        add_case(db, "b", store="S200", when=2)
        add_case(db, "c", store="S100", when=40)
        add_case(db, "d", store="S100", when=3, status="Closed")
        await db.commit()

        async def ids(**filters):
            result = await cases_for_error("UNMATCHED_SALE", 25, db, USER, **filters)
            return {c["id"] for c in result["items"]}

        assert await ids(store="S100") == {"a", "c"}
        # A store search matches anywhere in the store id.
        assert await ids(store="S1", status="all") == {"a", "c", "d"}
        assert await ids(store="00", status="all") == {"a", "b", "c", "d"}
        assert await ids(date_from=day(10)) == {"a", "b"}
        assert await ids(date_to=day(30)) == {"c"}
        assert await ids(store="S100", date_from=day(5), status="all") == {"a", "d"}
        assert await ids(store="%") == set()  # LIKE characters are taken literally

    run_with_db(scenario)


def test_each_case_reports_how_many_of_this_error_it_holds_and_ignores_other_errors():
    async def scenario(db):
        add_case(db, "multi", errors=3)
        add_case(db, "other", code="TAX_MISMATCH", errors=2)
        await db.commit()
        result = await cases_for_error("UNMATCHED_SALE", 25, db, USER)
        assert [(c["id"], c["errors"]) for c in result["items"]] == [("multi", 3)]
        assert (await cases_for_error("NO_SUCH_ERROR", 25, db, USER))["items"] == []

    run_with_db(scenario)
