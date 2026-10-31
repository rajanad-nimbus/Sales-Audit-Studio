import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.ingest import service
from models import Base, CanonicalTransaction, SourceRecord


@pytest.mark.parametrize("bad_detail", [
    {"lines": [{"item_code": "a", "quantity": "NaN", "unit_price": "1"}]},
    {"lines": [{"item_code": "a", "quantity": "1", "unit_price": "NaN"}]},
    {"lines": [{"item_code": "a", "quantity": "1", "unit_price": "1", "tax_rate": "bad"}]},
    {"taxes": [{"tax_code": "T", "rate": "NaN", "tax_amount": "1"}]},
    {"taxes": [{"tax_code": "T", "rate": "2", "tax_amount": "1"}]},
    {"tenders": [{"tender_type": "Cash", "amount": "NaN"}]},
    {"lines": [{"item_code": "a", "quantity": "1000000000", "unit_price": "1"}]},
    {"lines": [{"item_code": "a", "quantity": "0.0001", "unit_price": "1"}]},
    {"lines": [{"item_code": "a", "quantity": "1", "unit_price": "10000000000000"}]},
    {"lines": [{"item_code": "a", "quantity": "2", "unit_price": "6000000000000"}]},
    {"lines": [{"item_code": "a", "quantity": "1", "unit_price": "1", "tax_rate": "0.00001"}]},
    {"lines": {}},
    {"taxes": {}},
    {"discounts": ""},
    {"lines": [{"item_code": "a", "quantity": "1", "unit_price": "1", "description": {"invalid": True}}]},
    {"lines": [{"item_code": "a", "quantity": "1", "unit_price": "1", "tax_code": "T" * 31}]},
    {"discounts": [{"kind": "Coupon", "amount": "1", "code": ["invalid"]}]},
    {"tenders": [{"tender_type": "Cash", "amount": "1", "authorization": "A" * 101}]},
    {"taxes": [{"tax_code": "T", "tax_name": 12, "tax_amount": "1"}]},
    {"original_reference": {"invalid": True}},
    {"lines": [{"item_code": "a", "quantity": "1", "unit_price": "1"}],
     "discounts": [{"kind": "Coupon", "amount": "1", "line_no": True}]},
])
def test_bad_detail_is_quarantined_and_next_record_is_processed(bad_detail):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            session = async_sessionmaker(engine, expire_on_commit=False)
            base = {"event_time": "2026-10-01T10:00:00Z", "record_type": "Sale", "amount": "1",
                    "store_id": "S1"}
            async with session() as db:
                result = await service.ingest(db, "POS", "d1", [
                    {**base, "source_record_id": "bad", **bad_detail},
                    {**base, "source_record_id": "good"},
                ])
                assert result["quarantined"] == 1
                assert result["canonical_created"] == 1
                records = (await db.execute(select(SourceRecord).order_by(SourceRecord.source_record_id))).scalars().all()
                assert [record.status for record in records] == ["Quarantined", "Processed"]
                assert records[0].quarantine_reason.startswith("invalid transaction detail:")
                assert len((await db.execute(select(CanonicalTransaction))).scalars().all()) == 1
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_non_string_shopify_status_is_rejected():
    record = {"source_record_id": "1", "event_time": "2026-10-01T10:00:00Z", "record_type": "Order",
              "amount": "1", "store_id": "S1", "payment_reference": "P1", "financial_status": None}
    with pytest.raises(ValueError, match="financial_status"):
        service.parse("Shopify", record)


def test_ingest_validates_each_record_only_once(monkeypatch):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            session = async_sessionmaker(engine, expire_on_commit=False)
            calls = []
            parse = service.parse

            def track_parse(source, record):
                calls.append(record["source_record_id"])
                return parse(source, record)

            monkeypatch.setattr(service, "parse", track_parse)
            async with session() as db:
                await service.ingest(db, "POS", "d1", [
                    {"source_record_id": "valid", "event_time": "2026-10-01T10:00:00Z",
                     "record_type": "Sale", "amount": "1", "store_id": "S1"},
                    {"source_record_id": "invalid", "event_time": "not-a-date",
                     "record_type": "Sale", "amount": "1", "store_id": "S1"},
                ])
            assert calls == ["valid", "invalid"]
        finally:
            await engine.dispose()

    asyncio.run(run())
