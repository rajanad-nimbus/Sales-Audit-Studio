"""ReSA workbook tax allocation, through POS ingest and store-day re-total."""

import asyncio
from decimal import Decimal

from models import AuditTotal, AuditTotalDefinition, Base, StoreDay
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.ingest.service import ingest
from apps.store_days.routes import retotal_store_day
from apps.totals.client_totals import CLIENT_TOTALS


def test_workbook_tax_routes_store_amazon_and_ebay_registers_and_returns():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add(StoreDay(id="day", store_id="S1", business_date="2026-10-01"))
                db.add_all(AuditTotalDefinition(**t, revision=1) for t in CLIENT_TOTALS)
                records = []
                for register, tax in [
                    ("001", "1"),
                    ("892", "2"),
                    ("895", "3"),
                    ("896", "4"),
                    ("897", "5"),
                ]:
                    records.append(
                        {
                            "source_record_id": f"sale-{register}",
                            "record_type": "Sale",
                            "event_time": "2026-10-01T10:00:00Z",
                            "store_id": "S1",
                            "register_id": register,
                            "amount": str(Decimal("10") + Decimal(tax)),
                            "lines": [
                                {
                                    "item_code": "SKU",
                                    "quantity": "1",
                                    "unit_price": "10",
                                    "tax_code": "TAX",
                                    "tax_amount": tax,
                                }
                            ],
                        }
                    )
                records.append(
                    {
                        "source_record_id": "return-amazon",
                        "record_type": "Return",
                        "event_time": "2026-10-01T11:00:00Z",
                        "store_id": "S1",
                        "register_id": "892",
                        "amount": "5.50",
                        "lines": [
                            {
                                "item_code": "SKU",
                                "quantity": "1",
                                "unit_price": "5",
                                "tax_code": "TAX",
                                "tax_amount": "0.50",
                            }
                        ],
                    }
                )
                result = await ingest(db, "POS", "taxes", records)
                assert result["quarantined"] == 0
                await db.commit()
                await retotal_store_day(
                    "S1", "2026-10-01", db, {"user": "finance", "role": "finance"}
                )
                totals = (
                    (
                        await db.execute(
                            select(AuditTotal).where(
                                AuditTotal.store_day_id == "day",
                                AuditTotal.total_name.in_(
                                    [
                                        "Configured: SLSTAXACC",
                                        "Configured: SLSTXAMZN",
                                        "Configured: SLSTXEBAY",
                                    ]
                                ),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                assert {row.total_name: row.calculated_amount for row in totals} == {
                    "Configured: SLSTAXACC": Decimal("1"),
                    "Configured: SLSTXAMZN": Decimal("6.50"),
                    "Configured: SLSTXEBAY": Decimal("7"),
                }
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_uncoded_line_tax_is_retained_but_omitted_from_tax_total_without_tax_finding():
    """Characterize an uncoded, correctly rated line until source policy is settled."""
    from models import CanonicalTransaction, TransactionLine, TransactionTax
    from models import Exception as AuditException

    from apps.ingest import reconciliation

    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add(StoreDay(id="day", store_id="S1", business_date="2026-10-01"))
                db.add_all(AuditTotalDefinition(**t, revision=1) for t in CLIENT_TOTALS)
                record = {
                    "source_record_id": "uncoded-tax",
                    "record_type": "Sale",
                    "event_time": "2026-10-01T10:00:00Z",
                    "store_id": "S1",
                    "register_id": "001",
                    "amount": "11",
                    "lines": [
                        {
                            "item_code": "SKU",
                            "quantity": "1",
                            "unit_price": "10",
                            "tax_rate": "0.10",
                            "tax_amount": "1",
                        }
                    ],
                    "tenders": [{"tender_type": "Cash", "amount": "11"}],
                }
                result = await ingest(db, "POS", "uncoded", [record])
                assert result["quarantined"] == 0
                await reconciliation.reconcile(db)
                assert (
                    await db.execute(select(TransactionLine.tax_amount))
                ).scalar_one() == Decimal("1")
                assert (await db.execute(select(TransactionTax))).scalars().all() == []
                errors = (await db.execute(select(AuditException.exception_type))).scalars().all()
                assert "TAX_MISMATCH" not in errors
                assert (await db.execute(select(CanonicalTransaction))).scalars().one()
                await retotal_store_day(
                    "S1", "2026-10-01", db, {"user": "finance", "role": "finance"}
                )
                tax_total = (
                    await db.execute(
                        select(AuditTotal).where(AuditTotal.total_name == "Configured: SLSTAXACC")
                    )
                ).scalar_one()
                assert tax_total.calculated_amount == Decimal("0")
        finally:
            await engine.dispose()

    asyncio.run(run())
