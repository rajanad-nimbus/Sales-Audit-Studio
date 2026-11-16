"""The store day list pages and filters in the database, so it works for a large retailer."""

import asyncio
from decimal import Decimal

import pytest
from apps.store_days.routes import (
    list_store_days,
    store_day_brief,
    store_day_summary,
    store_days_needing_attention,
)
from fastapi import HTTPException, Response
from models import AuditTotal, Base, Case, FoundationReference, StoreDay, VarianceTolerance
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

FINANCE = {"user": "finance", "role": "finance"}


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


async def many_days(db, stores=30, days=10):
    for store in range(stores):
        for day in range(days):
            db.add(
                StoreDay(
                    id=f"d-{store}-{day}",
                    store_id=f"S{store:03d}",
                    business_date=f"2026-10-{day + 1:02d}",
                    status="Closed" if day < 4 else "Auditing",
                    totals_stale=(store % 10 == 0),
                )
            )
    await db.commit()


async def page(db, user=FINANCE, **filters):
    response = Response()
    args = {"store_id": None, "limit": 25, "page": 1, **filters}
    rows = await list_store_days(
        args.pop("store_id"), args.pop("limit"), db, user, response=response, **args
    )
    return rows, response.headers["X-Has-More"]


def test_the_list_returns_one_page_and_says_whether_more_follow():
    async def scenario(db):
        await many_days(db)  # 300 store days
        first, more = await page(db)
        assert len(first) == 25 and more == "true"
        last, more_at_end = await page(db, page=12)
        assert len(last) == 25 and more_at_end == "false"  # 300 = 12 pages of 25
        beyond, more_beyond = await page(db, page=13)
        assert beyond == [] and more_beyond == "false"

        # Newest first, and no row repeats or is skipped across pages.
        seen = []
        for number in range(1, 13):
            seen += [(r["business_date"], r["store_id"]) for r in (await page(db, page=number))[0]]
        assert len(seen) == len(set(seen)) == 300
        assert seen == sorted(seen, key=lambda k: (-int(k[0][-2:]), k[1]))
        assert seen[0][0] == "2026-10-10" and seen[-1][0] == "2026-10-01"

    run_with_db(scenario)


def test_filters_run_in_the_database_over_every_store_day_not_just_the_newest():
    async def scenario(db):
        await many_days(db)
        # A store day from the very first day is reachable, though 290 newer ones exist.
        old, _ = await page(db, store_id="S007", date_to="2026-10-01")
        assert [(r["store_id"], r["business_date"]) for r in old] == [("S007", "2026-10-01")]

        closed, _ = await page(db, status="Closed", limit=500)
        assert len(closed) == 30 * 4 and {r["status"] for r in closed} == {"Closed"}
        ranged, _ = await page(db, date_from="2026-10-09", date_to="2026-10-10", limit=500)
        assert len(ranged) == 60
        found, _ = await page(db, q="S02", limit=500)
        assert {r["store_id"] for r in found} == {f"S02{n}" for n in range(10)}
        # A search with LIKE characters is taken literally.
        assert (await page(db, q="%"))[0] == []
        with pytest.raises(HTTPException) as bad:
            await page(db, date_from="2026-13-40")
        assert bad.value.status_code == 422

    run_with_db(scenario)


def test_the_summary_gives_exact_counts_for_the_same_filters():
    async def scenario(db):
        await many_days(db)
        everything = await store_day_summary(None, None, None, None, db, FINANCE)
        assert everything == {
            "total": 300,
            "by_status": {"Closed": 120, "Auditing": 180},
            "totals_stale": 30,  # stores 0, 10 and 20, ten days each
            "unsigned_audit_versions": 0,  # none has current totals awaiting an auditor
        }
        narrowed = await store_day_summary(None, "2026-10-09", None, "S00", db, FINANCE)
        assert narrowed["total"] == 20 and narrowed["by_status"] == {"Auditing": 20}
        manager = {"user": "m", "role": "finance", "store_scoped": True, "stores": ["S001"]}
        assert (await store_day_summary(None, None, None, None, db, manager))["total"] == 10

    run_with_db(scenario)


def test_the_list_and_summary_only_cover_the_users_stores():
    async def scenario(db):
        await many_days(db, stores=5)
        manager = {"user": "m", "role": "finance", "store_scoped": True, "stores": ["S002"]}
        rows, more = await page(db, user=manager, limit=100)
        assert {r["store_id"] for r in rows} == {"S002"} and len(rows) == 10 and more == "false"

    run_with_db(scenario)


def test_late_stores_come_back_a_page_at_a_time_with_the_total():
    async def scenario(db):
        db.add_all(
            FoundationReference(
                id=f"f{n}", category="Store", code=f"S{n:04d}", name="x", active=True
            )
            for n in range(260)
        )
        await db.commit()
        first = await store_days_needing_attention(
            "2026-10-09", 3, 200, db, FINANCE, late_page=1, late_page_size=100
        )
        third = await store_days_needing_attention(
            "2026-10-09", 3, 200, db, FINANCE, late_page=3, late_page_size=100
        )
        assert first["late_total"] == 260 and first["expected_stores"] == 260
        assert len(first["late_stores"]) == 100 and len(third["late_stores"]) == 60
        assert first["late_stores"][0]["store_id"] == "S0000"
        assert third["late_stores"][0]["store_id"] == "S0200"

    run_with_db(scenario)


def test_the_brief_is_small_and_names_what_holds_the_day_back():
    async def scenario(db):
        db.add_all(
            [
                StoreDay(
                    id="d",
                    store_id="S1",
                    business_date="2026-10-01",
                    audit_version=1,
                    status="Auditing",
                ),
                VarianceTolerance(
                    id="r", within_amount=Decimal("1"), case_amount=Decimal("10"), active=True
                ),
            ]
        )
        await db.flush()
        for index in range(8):  # more variances than the brief lists
            db.add(
                AuditTotal(
                    id=f"t{index}",
                    store_day_id="d",
                    audit_version=1,
                    total_name=f"Total {index}",
                    level="Store",
                    calculated_amount=Decimal("100"),
                    declared_amount=Decimal("90"),
                    variance_amount=Decimal(index + 2),
                )
            )
        for index in range(7):
            db.add(
                Case(
                    id=f"c{index}",
                    case_number=f"C{index}",
                    case_type="X",
                    business_date="2026-10-01",
                    store_id="S1",
                    status="Open",
                    total_exposure=Decimal(index * 10),
                )
            )
        await db.commit()

        brief = await store_day_brief("S1", "2026-10-01", db, FINANCE)
        assert brief["variances"]["with_variance"] == 8
        assert len(brief["variances"]["top_unexplained"]) == 5  # bounded
        assert brief["variances"]["top_unexplained"][0]["variance"] == Decimal("9")  # largest first
        assert brief["cases"]["open"] == 7 and len(brief["cases"]["top"]) == 5
        assert brief["cases"]["top"][0]["case_number"] == "C6"  # most exposure first
        assert "7 open case(s)" in brief["blockers"]
        assert brief["readiness"]["audit"]["state"] == "Needs attention"
        assert brief["registers_with_gaps"] == 0

        other = {"user": "m", "role": "finance", "store_scoped": True, "stores": ["S9"]}
        with pytest.raises(HTTPException) as refused:
            await store_day_brief("S1", "2026-10-01", db, other)
        assert refused.value.status_code == 403

    run_with_db(scenario)
