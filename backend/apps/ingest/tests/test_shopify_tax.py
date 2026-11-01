import asyncio
from decimal import Decimal

import pytest
from apps.ingest import service
from apps.ingest.shopify import adapter as shopify_adapter
from models import Base, CanonicalTransaction, TransactionTax
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

ORDER = {"id": 1, "created_at": "2026-10-01T10:00:00Z", "total_price": "113.00", "currency": "USD", "financial_status": "paid",
         "tax_lines": [{"title": "VAT", "rate": 0.13, "price": "13.00"}]}
REFUND = {"id": 9, "created_at": "2026-10-02T10:00:00Z", "transactions": [{"kind": "refund", "amount": "56.50", "currency": "USD"}],
          "refund_line_items": [{"subtotal": "50.00", "total_tax": "6.50"}]}


def stored_taxes(records):
    async def go():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                result = await service.ingest(db, "Shopify", "d1", records)
                rows = (await db.execute(select(CanonicalTransaction.transaction_type, TransactionTax.tax_amount)
                                         .join(TransactionTax, TransactionTax.transaction_id == CanonicalTransaction.id))).all()
                return result, sorted(rows)
        finally:
            await engine.dispose()
    return asyncio.run(go())


def test_order_and_refund_tax_are_stored_signed_like_the_transaction():
    records = [shopify_adapter.normalize_order(ORDER, "S1"), shopify_adapter.normalize_refund(REFUND, 1, "USD", "S1")]
    result, rows = stored_taxes(records)
    assert result["quarantined"] == 0
    assert rows == [("Order", Decimal("13.00")), ("Refund", Decimal("-6.50"))]


def test_order_without_tax_lines_stores_no_tax_and_is_not_quarantined():
    result, rows = stored_taxes([shopify_adapter.normalize_order({**ORDER, "tax_lines": []}, "S1")])
    assert result["quarantined"] == 0 and rows == []


def test_malformed_empty_tax_container_is_quarantined():
    record = shopify_adapter.normalize_order(ORDER, "S1")
    record["taxes"] = {}

    result, rows = stored_taxes([record])

    assert result["quarantined"] == 1
    assert rows == []


@pytest.mark.parametrize("tax_line", [
    {"title": "VAT", "rate": "0.13005", "price": "13.00"},
    {"title": "VAT", "rate": "0.13", "price": "13.005"},
])
def test_shopify_tax_precision_is_preserved_for_ingest_validation(tax_line):
    record = shopify_adapter.normalize_order({**ORDER, "tax_lines": [tax_line]}, "S1")

    result, rows = stored_taxes([record])

    assert result["quarantined"] == 1
    assert rows == []
