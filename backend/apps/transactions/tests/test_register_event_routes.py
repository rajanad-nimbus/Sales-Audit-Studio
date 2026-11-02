import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from apps.ingest.models import RegisterEvent
from apps.transactions.register_event_routes import facets, list_register_events
from fastapi import HTTPException
from models import Base
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

USER = {"role": "analyst"}


def row(
    n, kind, store="4241", day="2026-10-01", register="001", reason=None, code=None, amount=None
):
    return RegisterEvent(
        id=f"e{n}",
        source_record_id=f"s{n}",
        source_format="rtlog",
        store_id=store,
        business_date=day,
        register_id=register,
        cashier_id=f"cashier-{n}",
        event_time=datetime(2026, 10, 1, 10, n, tzinfo=UTC),
        event_type=kind,
        transaction_number=str(n),
        reason_code=reason,
        amount=Decimal(amount) if amount else None,
        currency="USD",
        audit_code=code,
    )


def rows() -> list[RegisterEvent]:
    return [
        row(1, "no_sale"),
        row(2, "no_sale", register="002"),
        row(3, "paid_out", reason="SUPPLY", amount="25.00"),
        row(4, "paid_out", code="PAIDOUT_REASON_REQ", amount="40.00"),
        row(5, "void", store="9000", day="2026-10-02"),
    ]


def call(**kwargs):
    defaults = dict(
        store_id=None, event_type=None, flagged=False, date_from=None, date_to=None, q=None,
        sort="business_date", dir="desc", page=1, page_size=50,
    )  # fmt: skip
    user = kwargs.pop("user", USER)

    async def go():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add_all(rows())
                await db.commit()
                return await list_register_events(**{**defaults, **kwargs}, db=db, user=user)
        finally:
            await engine.dispose()

    return asyncio.run(go())


def test_the_list_counts_each_type_and_the_flagged_events():
    result = call()
    assert result["total"] == 5 and result["flagged"] == 1
    assert result["by_type"] == {"no_sale": 2, "paid_out": 2, "void": 1}


def test_the_type_tiles_ignore_the_selected_type_but_the_list_honors_it():
    result = call(event_type="paid_out")
    assert {r["event_type"] for r in result["items"]} == {"paid_out"} and result["total"] == 2
    assert result["by_type"] == {"no_sale": 2, "paid_out": 2, "void": 1}


def test_filters_narrow_the_list():
    assert call(flagged=True)["total"] == 1
    assert call(store_id="9000")["total"] == 1
    assert call(q="SUPPLY")["total"] == 1
    assert call(q="cashier-2")["total"] == 1
    assert call(q="002")["total"] == 1
    assert call(date_from=datetime(2026, 10, 2).date())["total"] == 1


def test_sorting_and_paging():
    by_amount = call(event_type="paid_out", sort="amount", dir="asc")
    assert [r["amount"] for r in by_amount["items"]] == ["25.00", "40.00"]
    page = call(page_size=2, page=3)
    assert (page["pages"], len(page["items"])) == (3, 1)


def test_a_reversed_date_range_is_refused():
    with pytest.raises(HTTPException) as caught:
        call(date_from=datetime(2026, 10, 3).date(), date_to=datetime(2026, 10, 1).date())
    assert caught.value.status_code == 422


def test_store_scoped_users_only_see_their_stores():
    scoped = {"role": "store_manager", "store_scoped": True, "stores": ["9000"]}
    result = call(user=scoped)
    assert result["total"] == 1 and result["by_type"] == {"void": 1}
    assert call(user={"role": "store_manager", "store_scoped": True, "stores": []})["total"] == 0


def test_facets_list_the_stores_types_and_dates():
    async def go():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add_all(rows())
                await db.commit()
                return await facets(db=db, user=USER)
        finally:
            await engine.dispose()

    assert asyncio.run(go()) == {
        "stores": ["4241", "9000"],
        "types": ["no_sale", "paid_out", "void"],
        "date_min": "2026-10-01",
        "date_max": "2026-10-02",
    }
