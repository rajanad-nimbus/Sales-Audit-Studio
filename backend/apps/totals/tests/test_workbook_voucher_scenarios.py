"""Workbook MCRCASHED item-range and return behavior through ingestion and re-total."""

import asyncio
from decimal import Decimal

from models import AuditTotal, AuditTotalDefinition, Base, StoreDay
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.ingest.service import ingest
from apps.store_days.routes import retotal_store_day
from apps.totals.client_totals import CLIENT_TOTALS


def test_mcr_cashed_uses_item_range_not_voucher_number_and_nets_returns():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add(StoreDay(id="day", store_id="S1", business_date="2026-10-01"))
                db.add_all(AuditTotalDefinition(**t, revision=1) for t in CLIENT_TOTALS)
                samples = [
                    ("lower", "100000000000", "Sale", "10", "GCN", "350000000000"),
                    ("upper", "299999999999", "Sale", "20", "GCN", "350000000000"),
                    ("below", "099999999999", "Sale", "40", "GCN", "150000000000"),
                    ("above", "300000000000", "Sale", "50", "GCN", "150000000000"),
                    ("wrong-type", "200000000000", "Sale", "60", "ITEM", "150000000000"),
                    ("return", "100000000000", "Return", "3", "GCN", "350000000000"),
                ]
                records = [
                    {
                        "source_record_id": name,
                        "record_type": kind,
                        "event_time": "2026-10-01T10:00:00Z",
                        "store_id": "S1",
                        "amount": amount,
                        "lines": [
                            {
                                "item_code": item,
                                "quantity": "1",
                                "unit_price": amount,
                                "source_attributes": {
                                    "item_type": item_type,
                                    "voucher_number": voucher,
                                },
                            }
                        ],
                    }
                    for name, item, kind, amount, item_type, voucher in samples
                ]
                outcome = await ingest(db, "POS", "mcr", records)
                assert outcome["quarantined"] == 0
                assert outcome["canonical_created"] == len(samples)
                await db.commit()
                await retotal_store_day(
                    "S1", "2026-10-01", db, {"user": "finance", "role": "finance"}
                )
                total = (
                    await db.execute(
                        select(AuditTotal).where(
                            AuditTotal.store_day_id == "day",
                            AuditTotal.total_name == "Configured: MCRCASHED",
                        )
                    )
                ).scalar_one()
                assert total.calculated_amount == Decimal("27")
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_mcr_declaration_and_difference_create_rule_case_only_when_unbalanced(install_controls):
    from integrations.ontology.audit_controls import TotalRule
    from models import Case

    from apps.ingest.pos_formats.standard import StandardLog
    from apps.ingest.register_events import ingest_register_events
    from apps.store_days.closures import close_blocker

    install_controls(
        total_rules=[
            TotalRule(
                id="mcr-difference",
                name="MCR difference",
                total_name="Configured: MCRCASHEDO",
                balance_level="Store",
                comparison="ABS_GT",
                threshold=Decimal("0"),
                threshold_currency="USD",
                severity="High",
            )
        ]
    )

    async def run(declared):
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add(StoreDay(id="day", store_id="S1", business_date="2026-10-01"))
                db.add_all(AuditTotalDefinition(**t, revision=1) for t in CLIENT_TOTALS)
                sale = {
                    "source_record_id": "sale",
                    "record_type": "Sale",
                    "event_time": "2026-10-01T10:00:00Z",
                    "store_id": "S1",
                    "amount": "27",
                    "lines": [
                        {
                            "item_code": "200000000000",
                            "quantity": "1",
                            "unit_price": "27",
                            "source_attributes": {"item_type": "GCN"},
                        }
                    ],
                }
                assert (await ingest(db, "POS", "sale", [sale]))["quarantined"] == 0
                event = {
                    "source_record_id": "total-1",
                    "store_id": "S1",
                    "business_date": "2026-10-01",
                    "register_id": "001",
                    "transaction_number": "1",
                    "event_time": "2026-10-01T11:00:00Z",
                    "cashier_id": "",
                    "salesperson_id": "",
                    "type": "total",
                    "rtlog_type": "TOTAL",
                    "sub_type": "",
                    "original_transaction_number": None,
                    "original_register_id": None,
                    "reason_code": None,
                    "vendor": None,
                    "value": declared,
                    "lines": [],
                    "discounts": [],
                    "taxes": [],
                    "tenders": [],
                    "details": {"user_references": [{"field": 1, "value": "MCRCASHEDP"}]},
                }
                log = StandardLog(
                    "rtlog", "S1", "2026-10-01", "2026-10-01T00:00:00Z", "POS", [event]
                )
                assert (await ingest_register_events(db, log))["created"] == 1
                await db.commit()
                await retotal_store_day(
                    "S1", "2026-10-01", db, {"user": "finance", "role": "finance"}
                )
                rows = (
                    (
                        await db.execute(
                            select(AuditTotal).where(
                                AuditTotal.total_name.in_(
                                    [
                                        "Configured: MCRCASHED",
                                        "Configured: MCRCASHEDP",
                                        "Configured: MCRCASHEDO",
                                    ]
                                )
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                amounts = {row.total_name: row.calculated_amount for row in rows}
                assert amounts == {
                    "Configured: MCRCASHED": Decimal("27"),
                    "Configured: MCRCASHEDP": Decimal(declared),
                    "Configured: MCRCASHEDO": Decimal("27") - Decimal(declared),
                }
                cases = (
                    (
                        await db.execute(
                            select(Case).where(Case.case_type == "Rule breach: MCR difference")
                        )
                    )
                    .scalars()
                    .all()
                )
                assert len(cases) == (declared != "27")
                if cases:
                    assert "case(s) remain open" in await close_blocker(
                        db, await db.get(StoreDay, "day")
                    )
        finally:
            await engine.dispose()

    for declared in ("25", "27", "29"):
        asyncio.run(run(declared))
