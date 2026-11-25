"""Workbook store-area and merchandise restrictions for sales clearing."""

import asyncio
from decimal import Decimal

import pytest
from models import AuditTotal, AuditTotalDefinition, Base, FoundationReference, StoreDay
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.ingest.service import ingest
from apps.store_days.routes import retotal_store_day
from apps.totals.client_totals import CLIENT_TOTALS


@pytest.mark.parametrize(
    ("area", "bsi", "outlet", "combined"),
    [
        (1999, 0, 0, 0),
        (2000, 70, 0, 70),
        (2999, 70, 0, 70),
        (3000, 0, 70, -70),
        (3999, 0, 70, -70),
        (4000, 0, 0, 0),
    ],
)
def test_sales_clearing_area_merchandise_discount_and_return(area, bsi, outlet, combined):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add(StoreDay(id="day", store_id="S1", business_date="2026-10-01"))
                db.add(
                    FoundationReference(
                        id="store",
                        category="Store",
                        code="S1",
                        name="Store",
                        active=True,
                        attributes={"area": area},
                    )
                )
                db.add_all(AuditTotalDefinition(**t, revision=1) for t in CLIENT_TOTALS)

                def record(name, item, amount, register="001", kind="Sale", item_type="ITEM"):
                    return {
                        "source_record_id": name,
                        "record_type": kind,
                        "event_time": "2026-10-01T10:00:00Z",
                        "store_id": "S1",
                        "register_id": register,
                        "amount": amount,
                        "lines": [
                            {
                                "item_code": item,
                                "quantity": "1",
                                "unit_price": amount,
                                "source_attributes": {"item_type": item_type},
                            }
                        ],
                    }

                main = record("sale", "SKU", "100")
                main["amount"] = "90"
                main["discounts"] = [{"kind": "Promotion", "line_no": 1, "amount": "10"}]
                excluded_item = "NM050BSI" if area < 3000 else "NM050OUT"
                records = [
                    main,
                    record("return", "SKU", "20", kind="Return"),
                    record("nonmerch", excluded_item, "30"),
                    record("gift", "GIFT", "40", item_type="GCN"),
                    record("remote", "SKU", "50", register="892"),
                ]
                outcome = await ingest(db, "POS", "clearing", records)
                assert outcome["quarantined"] == 0
                assert outcome["canonical_created"] == len(records)
                await db.commit()
                await retotal_store_day(
                    "S1", "2026-10-01", db, {"user": "finance", "role": "finance"}
                )
                names = ["SLSCLBSI", "SLSCLOUT", "SALESCLAS"]
                rows = (
                    (
                        await db.execute(
                            select(AuditTotal).where(
                                AuditTotal.total_name.in_([f"Configured: {n}" for n in names])
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                expected = {"SLSCLBSI": bsi, "SLSCLOUT": outlet, "SALESCLAS": combined}
                # Empty dimensions are omitted from persisted totals rather than stored as zero.
                assert {row.total_name: row.calculated_amount for row in rows} == {
                    f"Configured: {name}": Decimal(value)
                    for name, value in expected.items()
                    if value != 0
                }
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_no_sales_clearing_row_is_evaluated_as_zero_by_store_rule(install_controls):
    """A missing persisted value must still be a zero observation for a Store-level rule."""
    from integrations.ontology.audit_controls import TotalRule
    from models import Case

    from apps.store_days.closures import close_blocker

    install_controls(
        total_rules=[
            TotalRule(
                id="zero",
                name="No BSI sales",
                total_name="Configured: SLSCLBSI",
                balance_level="Store",
                comparison="EQ",
                threshold=Decimal("0"),
                threshold_currency="USD",
                severity="High",
            ),
            TotalRule(
                id="positive",
                name="Positive BSI sales",
                total_name="Configured: SLSCLBSI",
                balance_level="Store",
                comparison="GT",
                threshold=Decimal("0"),
                threshold_currency="USD",
                severity="High",
            ),
        ]
    )

    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add(StoreDay(id="day", store_id="S1", business_date="2026-10-01"))
                db.add(
                    FoundationReference(
                        id="store",
                        category="Store",
                        code="S1",
                        name="Store",
                        active=True,
                        attributes={"area": 2000},
                    )
                )
                db.add_all(AuditTotalDefinition(**t, revision=1) for t in CLIENT_TOTALS)
                record = {
                    "source_record_id": "remote",
                    "record_type": "Sale",
                    "event_time": "2026-10-01T10:00:00Z",
                    "store_id": "S1",
                    "register_id": "892",
                    "amount": "10",
                    "lines": [{"item_code": "SKU", "quantity": "1", "unit_price": "10"}],
                }
                assert (await ingest(db, "POS", "delivery", [record]))["quarantined"] == 0
                await db.commit()
                for _ in range(2):
                    await retotal_store_day(
                        "S1", "2026-10-01", db, {"user": "finance", "role": "finance"}
                    )
                totals = (
                    (
                        await db.execute(
                            select(AuditTotal).where(
                                AuditTotal.total_name == "Configured: SLSCLBSI"
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                assert totals == []
                cases = (
                    (await db.execute(select(Case).where(Case.case_type.like("Rule breach:%"))))
                    .scalars()
                    .all()
                )
                assert [case.case_type for case in cases] == ["Rule breach: No BSI sales"]
                assert cases[0].status == "Open"
                assert "case(s) remain open" in await close_blocker(
                    db, await db.get(StoreDay, "day")
                )
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_report_and_export_omit_clearing_total_when_no_row_exists():
    """Characterize current report/export representation after actual re-totaling."""
    from models import ExportDestination

    from apps.exports.selection import build
    from apps.reports.routes import flash_totals_report

    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                for total in CLIENT_TOTALS:
                    definition = AuditTotalDefinition(**total, revision=1)
                    if definition.name == "SLSCLBSI":
                        definition.report_usages = ["flash_totals"]
                    db.add(definition)
                for store, area, register in [("S1", 2000, "001"), ("S2", 2000, "892")]:
                    db.add(StoreDay(id=f"day-{store}", store_id=store, business_date="2026-10-01"))
                    db.add(
                        FoundationReference(
                            id=f"ref-{store}",
                            category="Store",
                            code=store,
                            name=store,
                            active=True,
                            attributes={"area": area},
                        )
                    )
                    record = {
                        "source_record_id": f"sale-{store}",
                        "record_type": "Sale",
                        "event_time": "2026-10-01T10:00:00Z",
                        "store_id": store,
                        "register_id": register,
                        "amount": "10",
                        "lines": [{"item_code": "SKU", "quantity": "1", "unit_price": "10"}],
                    }
                    assert (await ingest(db, "POS", f"delivery-{store}", [record]))[
                        "quarantined"
                    ] == 0
                await db.commit()
                for store in ("S1", "S2"):
                    await retotal_store_day(
                        store, "2026-10-01", db, {"user": "finance", "role": "finance"}
                    )
                report = await flash_totals_report(
                    db, {"user": "finance", "role": "finance"}, "2026-10-01", "2026-10-01", 0, 100
                )
                assert [(row[0], row[2], row[6]) for row in report["rows"]] == [
                    ("S1", "Configured: SLSCLBSI", Decimal("10"))
                ]
                destination = ExportDestination(id="dest", name="ERP", kind="file", config={})
                db.add(destination)
                # Isolate selection behavior: this test does not exercise actual closure.
                for store in ("S1", "S2"):
                    (await db.get(StoreDay, f"day-{store}")).status = "Closed"
                await db.commit()
                records, _, _, _ = await build(db, destination, "audited_totals")
                selected = [
                    (r["store_id"], r["calculated_amount"])
                    for r in records
                    if r["total_name"] == "Configured: SLSCLBSI"
                ]
                assert selected == [("S1", "10.00")]
        finally:
            await engine.dispose()

    asyncio.run(run())
