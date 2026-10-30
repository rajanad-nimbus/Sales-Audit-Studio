import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.ingest import service
from apps.totals.routes import preview_definition
from models import AuditTotalDefinition, Base


def preview(measure):
    async def go():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                sale = {"event_time": "2026-10-01T10:00:00Z", "store_id": "S1", "source_record_id": "T1", "payment_reference": "P1",
                        "lines": [{"item_code": "A", "quantity": "1", "unit_price": "100.00", "tax_code": "VAT", "tax_rate": "0.13"}]}
                await service.ingest(db, "POS", "d1", [{**sale, "record_type": "Sale", "amount": "113.00"},
                                                       {**sale, "source_record_id": "R1", "payment_reference": "P2", "record_type": "Return", "amount": "26.00",
                                                        "lines": [{"item_code": "A", "quantity": "1", "unit_price": "20.00", "tax_code": "VAT", "tax_rate": "0.30"}]}])
                db.add(AuditTotalDefinition(id="d", name="T", measure=measure, aggregation="Sum", source_system="POS",
                                            transaction_types=["Sale", "Return"], level="Store", active=True))
                await db.commit()
                return await preview_definition("d", "S1", "2026-10-01", db, {})
        finally:
            await engine.dispose()
    return asyncio.run(go())


def test_tax_total_sums_recorded_tax_signed_like_the_transaction():
    assert preview("Tax")["value"] == 13 - 6          # sale tax 13.00, return tax -6.00


def test_amount_total_still_sums_the_header_amount():
    assert preview("Amount")["value"] == 113 - 26
