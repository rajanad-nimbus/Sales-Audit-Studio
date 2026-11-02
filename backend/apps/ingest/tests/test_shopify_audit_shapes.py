"""Audit-error shapes Shopify will not let a store create, built as raw feed records."""

import asyncio

import pytest
from apps.ingest import service
from apps.ingest.shopify import adapter as sa
from models import Base, CanonicalTransaction
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

LINE = {"title": "Shirt", "price": "100.00", "quantity": 1, "taxable": True, "sku": "SHIRT"}
TAX = [{"title": "Sales tax", "rate": 0.1, "price": "10.00"}]


def feed_order(order_id=1, **extra):
    order = {
        "id": order_id,
        "created_at": "2026-10-01T10:00:00Z",
        "financial_status": "paid",
        "payment_gateway_names": ["manual"],
        "total_price": "110.00",
        "line_items": [LINE],
        "tax_lines": TAX,
        **extra,
    }
    return sa.normalize_order(order, "S1")


def ingest(records):
    async def go():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                result = await service.ingest(db, "Shopify", "d1", records)
                rows = (
                    (await db.execute(select(CanonicalTransaction.detail_status))).scalars().all()
                )
                return result, sorted(rows)
        finally:
            await engine.dispose()

    return asyncio.run(go())


def test_clean_order_is_balanced():
    result, statuses = ingest([feed_order()])
    assert result["quarantined"] == 0 and statuses == ["Balanced"]


@pytest.mark.parametrize(
    ("name", "extra"),
    [
        ("header total above the lines", {"total_price": "125.00"}),
        ("header total below the lines", {"total_price": "99.00"}),
        (
            "tax lines do not match the total",
            {"total_price": "110.00", "tax_lines": [{**TAX[0], "price": "7.00"}]},
        ),
    ],
)
def test_total_mismatches_are_unbalanced(name, extra):
    _, statuses = ingest([feed_order(**extra)])
    assert statuses == ["Unbalanced"], name


def test_short_and_over_tenders_are_unbalanced():
    short = [
        {"id": 1, "kind": "sale", "status": "success", "gateway": "manual", "amount": "100.00"}
    ]
    over = [{"id": 1, "kind": "sale", "status": "success", "gateway": "manual", "amount": "125.00"}]
    for transactions in (short, over):
        record = sa.normalize_order(
            {
                "id": 1,
                "created_at": "2026-10-01T10:00:00Z",
                "financial_status": "paid",
                "total_price": "110.00",
                "line_items": [LINE],
                "tax_lines": TAX,
            },
            "S1",
            transactions,
        )
        assert ingest([record])[1] == ["Unbalanced"]


def test_unknown_and_missing_items_are_stored_with_a_visible_code():
    record = feed_order(line_items=[{**LINE, "sku": ""}])
    assert record["lines"][0]["item_code"] == "NO-SKU"
    assert ingest([record])[0]["quarantined"] == 0


def test_negative_priced_line_is_quarantined():
    record = feed_order(line_items=[{**LINE, "price": "-5.00"}], total_price="-5.00", tax_lines=[])
    result, statuses = ingest([record])
    assert result["quarantined"] == 1 and statuses == []


def test_the_same_order_delivered_twice_is_stored_once():
    _, statuses = ingest([feed_order(), feed_order()])
    assert statuses == ["Balanced"]
