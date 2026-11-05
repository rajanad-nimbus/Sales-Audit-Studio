"""Shopify day totals (Shopify's end-of-day figure per store day) and duplicate Shopify orders."""

import asyncio
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from apps.ingest import reconciliation
from apps.ingest import service as ingest
from apps.ingest.models import ReconciliationPolicy
from apps.ingest.shopify import adapter as sa
from apps.ingest.shopify import day_totals
from models import Base, CanonicalTransaction, Case, SourceRecord
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

DAY = "2024-01-15"


def run(test):
    async def go():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                return await test(db)
        finally:
            await engine.dispose()

    return asyncio.run(go())


def order(i, amount="100.00", at=f"{DAY}T10:00:00Z", record_id=None, **extra):
    return {
        "source_record_id": record_id or f"SHOP-{i}",
        "record_type": "Order",
        "store_id": "S1",
        "event_time": at,
        "amount": amount,
        "currency": "USD",
        "payment_reference": str(i),
        "financial_status": "paid",
        **extra,
    }


def refund(rid, order_id, amount):
    return {
        "source_record_id": f"SHREF-{rid}",
        "record_type": "Refund",
        "store_id": "S1",
        "event_time": f"{DAY}T15:00:00Z",
        "amount": amount,
        "currency": "USD",
        "payment_reference": str(order_id),
        "order_id": str(order_id),
        "refund_id": rid,
    }


def day_total(order_count, order_total, record_id="EOD-1", **extra):
    return {
        "source_record_id": record_id,
        "record_type": "DayTotal",
        "store_id": "S1",
        "business_date": DAY,
        "currency": "USD",
        "order_count": order_count,
        "order_total": order_total,
        **extra,
    }


async def reconcile(db, store_ids=None):
    await reconciliation.reconcile(db, DAY, store_ids=store_ids)
    await db.commit()
    return sorted((await db.execute(select(Case.case_type))).scalars().all())


async def statuses(db, record_type="DayTotal"):
    rows = await db.execute(select(SourceRecord).where(SourceRecord.source_system == "Shopify"))
    return {
        r.source_record_id: (r.status, r.quarantine_reason)
        for r in rows.scalars()
        if f'"record_type": "{record_type}"' in r.payload.decode()
    }


# --- the day total record -------------------------------------------------------------------


def test_day_total_is_archived_without_creating_a_transaction():
    async def test(db):
        stats = await ingest.ingest(db, "Shopify", "d", [day_total(2, "150.00")])
        assert stats["archived"] == 1 and stats["canonical_created"] == 0
        assert (await db.execute(select(CanonicalTransaction))).scalars().all() == []
        assert (await statuses(db))["EOD-1"] == ("Archived", None)

    run(test)


@pytest.mark.parametrize(
    ("extra", "reason"),
    [
        ({"order_count": -1}, "order_count must be a non-negative"),
        ({"order_count": True}, "order_count must be a non-negative integer"),
        ({"order_total": "-5.00"}, "order_total must be non-negative"),
        ({"order_total": "1.005"}, "more than two decimal places"),
        ({"business_date": "2024-1-15"}, "business_date must be a valid YYYY-MM-DD date"),
        ({"refund_count": 1}, "refund_count and refund_total must be supplied together"),
    ],
)
def test_invalid_day_total_is_quarantined(extra, reason):
    async def test(db):
        stats = await ingest.ingest(db, "Shopify", "d", [{**day_total(1, "10.00"), **extra}])
        assert stats["quarantined"] == 1
        status, why = (await statuses(db))["EOD-1"]
        assert status == "Quarantined" and reason in why

    run(test)


# --- store day against Shopify's day total --------------------------------------------------


def test_store_day_that_agrees_with_shopify_raises_nothing():
    async def test(db):
        await ingest.ingest(db, "Shopify", "d", [order(1), order(2, "50.00")])
        await ingest.ingest(db, "Shopify", "eod", [day_total(2, "150.00")])
        assert await reconcile(db) == []
        assert (await statuses(db))["EOD-1"] == ("Processed", None)

    run(test)


def test_order_edited_after_import_is_a_store_day_total_mismatch():
    """Shopify's resend of an edited order is quarantined, so only the day total sees the new amount."""

    async def test(db):
        await ingest.ingest(db, "Shopify", "d1", [order(1), order(2, "50.00")])
        stats = await ingest.ingest(db, "Shopify", "d2", [order(2, "65.00")])
        assert stats["quarantined"] == 1
        await ingest.ingest(db, "Shopify", "eod", [day_total(2, "165.00")])
        assert await reconcile(db) == ["Shopify Store Day Total Mismatch"]
        case = (await db.execute(select(Case))).scalar_one()
        assert case.total_exception_amount == Decimal("15.00") and case.store_id == "S1"

    run(test)


def test_fewer_orders_than_shopify_reports_is_an_incomplete_feed():
    async def test(db):
        await ingest.ingest(db, "Shopify", "d", [order(1)])
        await ingest.ingest(db, "Shopify", "eod", [day_total(3, "180.00")])
        assert await reconcile(db) == ["Shopify Incomplete Feed"]
        case = (await db.execute(select(Case))).scalar_one()
        assert case.total_exception_amount == Decimal("80.00")

    run(test)


def test_more_orders_than_shopify_reports_is_a_mismatch_even_when_totals_agree():
    async def test(db):
        await ingest.ingest(db, "Shopify", "d", [order(1, "60.00"), order(2, "40.00")])
        await ingest.ingest(db, "Shopify", "eod", [day_total(1, "100.00")])
        assert await reconcile(db) == ["Shopify Store Day Total Mismatch"]

    run(test)


def test_day_with_orders_shopify_says_it_never_had_is_a_mismatch():
    async def test(db):
        await ingest.ingest(db, "Shopify", "d", [order(1)])
        await ingest.ingest(db, "Shopify", "eod", [day_total(0, "0")])
        assert await reconcile(db) == ["Shopify Store Day Total Mismatch"]

    run(test)


def test_only_the_latest_revision_of_a_day_total_is_checked():
    async def test(db):
        await ingest.ingest(db, "Shopify", "d", [order(1)])
        await ingest.ingest(db, "Shopify", "eod1", [day_total(1, "90.00", record_id="EOD-old")])
        await ingest.ingest(db, "Shopify", "eod2", [day_total(1, "100.00", record_id="EOD-new")])
        assert await reconcile(db) == []
        found = await statuses(db)
        assert found["EOD-old"][0] == "Superseded" and found["EOD-new"][0] == "Processed"

    run(test)


def test_refund_figures_are_checked_when_the_day_total_carries_them():
    async def test(db):
        await ingest.ingest(db, "Shopify", "d", [order(1), refund("r1", 1, "30.00")])
        await ingest.ingest(
            db,
            "Shopify",
            "eod",
            [day_total(1, "100.00", refund_count=1, refund_total="45.00")],
        )
        assert "Shopify Store Day Total Mismatch" in await reconcile(db)

    run(test)


def test_refunds_are_not_compared_when_the_day_total_has_none():
    async def test(db):
        await ingest.ingest(db, "Shopify", "d", [order(1), refund("r1", 1, "30.00")])
        await ingest.ingest(db, "Shopify", "eod", [day_total(1, "100.00")])
        assert "Shopify Store Day Total Mismatch" not in await reconcile(db)

    run(test)


def test_policy_amount_tolerance_applies_in_its_currency():
    async def test(db):
        db.add(
            ReconciliationPolicy(
                id=str(uuid.uuid4()),
                name="default",
                amount_tolerance=Decimal("0.05"),
                amount_tolerance_currency="USD",
                settlement_day_tolerance=0,
                active=True,
            )
        )
        await ingest.ingest(db, "Shopify", "d", [order(1)])
        await ingest.ingest(db, "Shopify", "eod", [day_total(1, "100.04")])
        assert await reconcile(db) == []

    run(test)


def test_a_store_outside_the_reconciliation_scope_keeps_its_day_total_pending():
    async def test(db):
        await ingest.ingest(db, "Shopify", "d", [order(1)])
        await ingest.ingest(db, "Shopify", "eod", [day_total(5, "999.00")])
        assert await reconcile(db, store_ids={"OTHER"}) == []
        assert (await statuses(db))["EOD-1"][0] == "Archived"

    run(test)


# --- duplicate orders -----------------------------------------------------------------------


def test_same_shopify_order_stored_twice_flags_the_later_copy():
    async def test(db):
        await ingest.ingest(db, "Shopify", "connector", [order(1)])
        await ingest.ingest(db, "Shopify", "upload", [order(1, record_id="UPLOAD-1001")])
        assert await reconcile(db) == ["Duplicate Shopify Order"]
        rows = (
            await db.execute(
                select(
                    SourceRecord.source_record_id, CanonicalTransaction.reconciliation_status
                ).join(
                    CanonicalTransaction, CanonicalTransaction.source_record_id == SourceRecord.id
                )
            )
        ).all()
        assert dict(rows) == {"SHOP-1": "Matched", "UPLOAD-1001": "Exception"}

    run(test)


LINE = {"title": "Shirt", "price": "100.00", "quantity": 1, "taxable": False, "sku": "SHIRT"}


def shop_order(order_id, minute, customer=77, items=(LINE,), status="paid", total="100.00"):
    return sa.normalize_order(
        {
            "id": order_id,
            "created_at": f"{DAY}T10:{minute:02d}:00Z",
            "financial_status": status,
            "payment_gateway_names": ["manual"],
            "total_price": total,
            "line_items": list(items),
            "customer": {"id": customer} if customer else None,
        },
        "S1",
    )


@pytest.mark.parametrize(
    ("second", "flagged"),
    [
        ({"order_id": 2, "minute": 3}, True),
        ({"order_id": 2, "minute": 30}, False),
        ({"order_id": 2, "minute": 3, "customer": 88}, False),
        ({"order_id": 2, "minute": 3, "status": "voided"}, False),
        (
            {
                "order_id": 2,
                "minute": 3,
                "items": ({**LINE, "sku": "HAT", "title": "Hat"},),
            },
            False,
        ),
    ],
    ids=["same-basket-3-min", "30-min-apart", "other-customer", "voided", "other-items"],
)
def test_possible_double_submission(second, flagged):
    async def test(db):
        await ingest.ingest(db, "Shopify", "d", [shop_order(1, 0), shop_order(**second)])
        cases = await reconcile(db)
        assert ("Possible Duplicate Shopify Order" in cases) is flagged

    run(test)


def test_guest_orders_are_never_possible_duplicates():
    async def test(db):
        await ingest.ingest(
            db, "Shopify", "d", [shop_order(1, 0, customer=None), shop_order(2, 1, customer=None)]
        )
        assert "Possible Duplicate Shopify Order" not in await reconcile(db)

    run(test)


def test_a_double_submission_is_found_against_an_order_from_an_earlier_run():
    async def test(db):
        await ingest.ingest(db, "Shopify", "d1", [shop_order(1, 0)])
        assert await reconcile(db) == []
        await ingest.ingest(db, "Shopify", "d2", [shop_order(2, 4)])
        assert await reconcile(db) == ["Possible Duplicate Shopify Order"]

    run(test)


# --- building day totals during a sync ------------------------------------------------------


def test_covered_days_are_whole_local_days_inside_the_window():
    zone = ZoneInfo("America/New_York")
    start = datetime(2024, 1, 14, 12, tzinfo=UTC)  # 07:00 local on the 14th
    end = datetime(2024, 1, 17, 6, tzinfo=UTC)  # 01:00 local on the 17th
    days = [day for day, _start in day_totals.covered_days(start, end, zone)]
    assert days == [date(2024, 1, 15), date(2024, 1, 16)]


def test_multi_currency_day_uses_listed_counts_per_currency():
    tally = day_totals.OrderTally()
    for currency, total in (("USD", "10.00"), ("USD", "5.00"), ("CAD", "7.00")):
        tally.add({"created_at": f"{DAY}T10:00:00Z", "currency": currency, "total_price": total})
    records = day_totals.day_total_records("S1", "USD", {date(2024, 1, 15): 9}, tally)
    figures = {r["currency"]: (r["order_count"], r["order_total"]) for r in records}
    assert figures == {"CAD": (1, "7.00"), "USD": (2, "15.00")}


def mock_shopify(count):
    calls = []

    async def shop(_request):
        return web.json_response(
            {"shop": {"id": 1, "name": "S", "currency": "USD", "iana_timezone": "UTC"}}
        )

    async def orders(_request):
        return web.json_response(
            {
                "orders": [
                    {
                        "id": i,
                        "created_at": f"{DAY}T1{i}:00:00Z",
                        "total_price": "10.00",
                        "currency": "USD",
                        "financial_status": "paid",
                    }
                    for i in range(1, 4)
                ]
            }
        )

    async def order_count(request):
        calls.append(dict(request.query))
        return web.json_response({"count": count})

    app = web.Application()
    app.router.add_get("/admin/api/2024-01/shop.json", shop)
    app.router.add_get("/admin/api/2024-01/orders.json", orders)
    app.router.add_get("/admin/api/2024-01/orders/count.json", order_count)
    return app, calls


def fetch_with_day_totals(monkeypatch, count, start, end):
    app, calls = mock_shopify(count)

    async def go():
        server = TestServer(app)
        await server.start_server()
        try:
            monkeypatch.setenv("SHOPIFY_STORE_URL", f"http://{server.host}:{server.port}")
            monkeypatch.setenv("SHOPIFY_ACCESS_TOKEN", "tok")
            return await sa.fetch_shopify_feed(
                "S1", created_at_min=start, created_at_max=end, day_totals=True
            )
        finally:
            await server.close()

    feed = asyncio.run(go())
    return [r for r in feed["Shopify"] if r["record_type"] == "DayTotal"], calls


def test_sync_declares_shopify_count_and_listed_total_for_each_covered_day(monkeypatch):
    totals, calls = fetch_with_day_totals(
        monkeypatch,
        4,
        datetime(2024, 1, 15, tzinfo=UTC),
        datetime(2024, 1, 16, tzinfo=UTC),
    )
    assert len(totals) == 1
    total = totals[0]
    assert (total["business_date"], total["order_count"], total["order_total"]) == (
        DAY,
        4,  # Shopify's count, not the three orders the list returned
        "30.00",
    )
    assert total["source_record_id"].startswith(f"SHOPEOD-S1-{DAY}-USD-")
    assert calls == [
        {
            "status": "any",
            "processed_at_min": "2024-01-15T00:00:00+00:00",
            "processed_at_max": "2024-01-15T23:59:59+00:00",
        }
    ]


def test_a_backdated_order_counts_on_the_day_it_was_processed():
    tally = day_totals.OrderTally()
    tally.add(
        {
            "created_at": "2024-01-16T08:00:00-05:00",
            "processed_at": "2024-01-15T10:00:00-05:00",
            "currency": "USD",
            "total_price": "12.50",
        }
    )
    assert tally.on(date(2024, 1, 15)) == {"USD": [1, Decimal("12.50")]}
    assert tally.on(date(2024, 1, 16)) == {}


def test_sync_declares_nothing_for_a_partly_covered_day(monkeypatch):
    totals, calls = fetch_with_day_totals(
        monkeypatch,
        3,
        datetime(2024, 1, 15, 6, tzinfo=UTC),
        datetime(2024, 1, 16, 6, tzinfo=UTC),
    )
    assert totals == [] and calls == []


def test_sync_day_totals_round_trip_through_ingest_and_reconcile(monkeypatch):
    totals, _calls = fetch_with_day_totals(
        monkeypatch,
        3,
        datetime(2024, 1, 15, tzinfo=UTC),
        datetime(2024, 1, 16, tzinfo=UTC),
    )

    async def test(db):
        orders = [order(i, "10.00", at=f"{DAY}T1{i}:00:00Z") for i in range(1, 4)]
        await ingest.ingest(db, "Shopify", "d", [*orders, *totals])
        assert await reconcile(db) == []

    run(test)
