import asyncio
import uuid

import pytest
from apps.controls.foundation.models import FoundationReference
from apps.ingest import reconciliation, service
from apps.ingest.shopify import adapter as sa
from models import Base
from models import Exception as DBException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

LINE_RULES = {"SKU_NOT_FOUND", "TAX_MISMATCH", "RETURN_OVER_SOLD"}


def item(sku="SHIRT", price="100.00", qty=1, **extra):
    return {"title": sku, "price": price, "quantity": qty, "taxable": True, "sku": sku, **extra}


def order(order_id=1, items=None, total="110.00", **extra):
    return sa.normalize_order(
        {
            "id": order_id,
            "created_at": "2026-10-01T10:00:00Z",
            "financial_status": "paid",
            "currency": "USD",
            "payment_gateway_names": ["manual"],
            "total_price": total,
            "line_items": items or [item()],
            "tax_lines": [{"title": "Sales tax", "rate": 0.1, "price": "10.00"}],
            **extra,
        },
        "S1",
    )


def refund(refund_id, order_id, qty, subtotal, tax, sku="SHIRT"):
    amount = f"{float(subtotal) + float(tax):.2f}"
    return sa.normalize_refund(
        {
            "id": refund_id,
            "created_at": "2026-10-02T10:00:00Z",
            "transactions": [
                {"kind": "refund", "status": "success", "gateway": "manual", "amount": amount}
            ],
            "refund_line_items": [
                {"quantity": qty, "subtotal": subtotal, "total_tax": tax, "line_item": item(sku)}
            ],
        },
        order_id,
        "USD",
        "S1",
    )


def audit(*deliveries, catalog=()):
    """Ingest each delivery and reconcile once at the end; return the line-rule exception types."""

    async def go():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                for code in catalog:
                    db.add(
                        FoundationReference(
                            id=str(uuid.uuid4()), category="Item", code=code, name=code, active=True
                        )
                    )
                for number, delivery in enumerate(deliveries, 1):
                    source, records = (
                        delivery if isinstance(delivery, tuple) else ("Shopify", delivery)
                    )
                    await service.ingest(db, source, f"d{number}", list(records))
                await reconciliation.reconcile(db)
                types = (await db.execute(select(DBException.exception_type))).scalars().all()
                return sorted(set(types) & LINE_RULES)
        finally:
            await engine.dispose()

    return asyncio.run(go())


def test_ordinary_orders_raise_no_line_rule():
    allocation = lambda amount, index: [{"amount": amount, "discount_application_index": index}]  # noqa: E731
    discounted = order(
        2,
        [item("JEANS", "80.00", discount_allocations=allocation("8.00", 0))],
        total="79.20",
        shipping_lines=[
            {"title": "Standard", "price": "9.00", "discount_allocations": allocation("9.00", 1)}
        ],
        discount_applications=[
            {"type": "automatic", "title": "Spring"},
            {"type": "discount_code", "code": "SHIP"},
        ],
        tax_lines=[{"title": "Sales tax", "rate": 0.1, "price": "7.20"}],
    )
    inclusive = order(
        3, [item("COAT", "80.00")], total="80.00", taxes_included=True,
        tax_lines=[{"title": "VAT", "rate": 0.13, "price": "9.20"}],
    )  # fmt: skip
    assert audit([order(1), discounted, inclusive]) == []


def test_item_with_no_sku_is_flagged():
    assert audit([order(items=[item("")])]) == ["SKU_NOT_FOUND"]


def test_item_missing_from_a_synced_item_master_is_flagged():
    assert audit([order()], catalog=["OTHER"]) == ["SKU_NOT_FOUND"]
    assert audit([order()], catalog=["SHIRT"]) == []


def test_tax_that_does_not_match_the_rates_is_flagged():
    bad = order(tax_lines=[{"title": "Sales tax", "rate": 0.1, "price": "7.00"}], total="107.00")
    assert audit([bad]) == ["TAX_MISMATCH"]


def two_item_order():
    """Two different items, so a refund of one item twice stays within the order's money."""
    return order(
        items=[item("SHIRT"), item("JEANS")],
        total="220.00",
        tax_lines=[{"title": "Sales tax", "rate": 0.1, "price": "20.00"}],
    )


def test_return_of_more_than_was_sold_is_flagged():
    over = refund(10, 1, qty=2, subtotal="200.00", tax="20.00")
    assert audit([two_item_order(), over]) == ["RETURN_OVER_SOLD"]


def test_return_within_the_quantity_sold_is_not_flagged():
    assert audit([two_item_order(), refund(10, 1, qty=1, subtotal="100.00", tax="10.00")]) == []


@pytest.mark.parametrize("separate_deliveries", [True, False])
def test_two_returns_that_together_exceed_the_sale_are_flagged(separate_deliveries):
    first = refund(10, 1, qty=1, subtotal="100.00", tax="10.00")
    second = refund(11, 1, qty=1, subtotal="100.00", tax="10.00")
    sale = two_item_order()
    deliveries = [[sale, first], [second]] if separate_deliveries else [[sale, first, second]]
    assert audit(*deliveries) == ["RETURN_OVER_SOLD"]


def pos(record_id, record_type, original=None, qty=1, amount="110.00"):
    return {
        "source_record_id": record_id,
        "record_type": record_type,
        "event_time": "2026-10-01T10:00:00Z",
        "amount": amount,
        "store_id": "S1",
        "payment_reference": f"PAY-{record_id}",
        "lines": [
            {
                "item_code": "SHIRT",
                "quantity": str(qty),
                "unit_price": "100.00",
                "tax_amount": f"{10 * qty}.00",
            }
        ],
        "tenders": [{"tender_type": "Cash", "amount": amount}],
        **({"original_reference": original} if original else {}),
    }


def test_pos_return_of_more_than_was_sold_is_flagged():
    sale = pos("T1", "Sale")
    over = pos("R1", "Return", original="PAY-T1", qty=2, amount="220.00")
    assert audit(("POS", [sale, over])) == ["RETURN_OVER_SOLD"]


def test_pos_return_within_the_quantity_sold_is_not_flagged():
    sale = pos("T1", "Sale")
    ok = pos("R1", "Return", original="PAY-T1")
    assert audit(("POS", [sale, ok])) == []
