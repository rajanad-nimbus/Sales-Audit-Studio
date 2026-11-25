"""Workbook remote-register merchandise and non-merchandise calculations."""

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


@pytest.mark.parametrize("register", ["880", "881", "889", "893", "892", "895", "896", "897"])
@pytest.mark.parametrize(
    ("area", "merch_total", "nonmerch_total", "excluded_item"),
    [
        (2000, "REMSLSBSI", "REMNMSBSI", "NM050BSI"),
        (3000, "REMSLSOUT", "REMNMOUT", "NM050OUT"),
    ],
)
def test_remote_merchandise_and_nonmerchandise_totals(
    register, area, merch_total, nonmerch_total, excluded_item
):
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

                def row(name, item, amount, reg=register, kind="Sale", item_type="ITEM"):
                    return {
                        "source_record_id": name,
                        "record_type": kind,
                        "event_time": "2026-10-01T10:00:00Z",
                        "store_id": "S1",
                        "register_id": reg,
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

                sale = row("sale", "SKU", "100")
                sale["amount"] = "90"
                sale["discounts"] = [{"kind": "Promotion", "line_no": 1, "amount": "10"}]
                records = [
                    sale,
                    row("return", "SKU", "20", kind="Return"),
                    row("nonmerch", excluded_item, "30"),
                    row("gift", "GIFT", "40", item_type="GCN"),
                    row("local", "SKU", "50", reg="001"),
                ]
                outcome = await ingest(db, "POS", "remote", records)
                assert outcome["quarantined"] == 0 and outcome["canonical_created"] == len(records)
                await db.commit()
                await retotal_store_day(
                    "S1", "2026-10-01", db, {"user": "finance", "role": "finance"}
                )
                names = [f"Configured: {merch_total}", f"Configured: {nonmerch_total}"]
                totals = (
                    (await db.execute(select(AuditTotal).where(AuditTotal.total_name.in_(names))))
                    .scalars()
                    .all()
                )
                assert {row.total_name: row.calculated_amount for row in totals} == {
                    names[0]: Decimal("70"),
                    names[1]: Decimal("30"),
                }
        finally:
            await engine.dispose()

    asyncio.run(run())


# Transcribed from the Sales sheet comments, independently of the configured restrictions.
WORKBOOK_NONMERCH_NUMBERS = (
    "050",
    "842",
    "857",
    "971",
    "984",
    "981",
    "972",
    "991",
    "895",
    "846",
    "840",
    "831",
    "841",
    "832",
    "858",
    "847",
    "849",
    "835",
    "051",
    "052",
    "853",
    "854",
    "877",
    "887",
    "848",
    "894",
    "977",
    "978",
    "893",
    "845",
)


@pytest.mark.parametrize(
    ("area", "suffix", "remote_nonmerch", "remote_merch", "store_merch"),
    [
        (2000, "BSI", "REMNMSBSI", "REMSLSBSI", "SLSCLBSI"),
        (3000, "OUT", "REMNMOUT", "REMSLSOUT", "SLSCLOUT"),
    ],
)
def test_every_workbook_nonmerch_item_is_included_or_excluded_as_specified(
    area, suffix, remote_nonmerch, remote_merch, store_merch
):
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
                codes = [f"NM{number}{suffix}" for number in WORKBOOK_NONMERCH_NUMBERS]
                assert len(codes) == len(set(codes)) == 30
                records = []
                for register in ("001", "892"):
                    for code in [*codes, f"NM999{suffix}"]:
                        records.append(
                            {
                                "source_record_id": f"{register}-{code}",
                                "record_type": "Sale",
                                "event_time": "2026-10-01T10:00:00Z",
                                "store_id": "S1",
                                "register_id": register,
                                "amount": "1",
                                "lines": [{"item_code": code, "quantity": "1", "unit_price": "1"}],
                            }
                        )
                result = await ingest(db, "POS", "all-items", records)
                assert result["quarantined"] == 0 and result["canonical_created"] == 62
                await db.commit()
                await retotal_store_day(
                    "S1", "2026-10-01", db, {"user": "finance", "role": "finance"}
                )
                names = [
                    f"Configured: {name}" for name in (remote_nonmerch, remote_merch, store_merch)
                ]
                rows = (
                    (await db.execute(select(AuditTotal).where(AuditTotal.total_name.in_(names))))
                    .scalars()
                    .all()
                )
                assert {row.total_name: row.calculated_amount for row in rows} == {
                    names[0]: Decimal("30"),
                    names[1]: Decimal("1"),
                    names[2]: Decimal("1"),
                }
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_nonmerchandise_restrictions_equal_each_workbook_list():
    by_name = {total["name"]: total for total in CLIENT_TOTALS}
    for suffix, include, remote_exclude, store_exclude in (
        ("BSI", "REMNMSBSI", "REMSLSBSI", "SLSCLBSI"),
        ("OUT", "REMNMOUT", "REMSLSOUT", "SLSCLOUT"),
    ):
        expected = {f"NM{number}{suffix}" for number in WORKBOOK_NONMERCH_NUMBERS}
        for name, operator in (
            (include, "in"),
            (remote_exclude, "not_in"),
            (store_exclude, "not_in"),
        ):
            restrictions = [
                r
                for r in by_name[name]["restrictions"]
                if r["realm"] == "Line" and r["field"] == "item_code"
            ]
            assert len(restrictions) == 1
            assert restrictions[0]["operator"] == operator
            assert set(restrictions[0]["value"].split(",")) == expected


def test_remote_clearing_subtracts_expenses_and_marketplace_shipping():
    """Workbook REMSLSCL subtracts the listed non-merchandise and shipping components."""

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
                samples = [
                    ("expense", "NM842BSI", "001", "10"),
                    ("postage-expense", "NM894BSI", "001", "5"),
                    ("amazon-shipping", "NM877BSI", "892", "7"),
                ]
                records = [
                    {
                        "source_record_id": name,
                        "record_type": "Sale",
                        "event_time": "2026-10-01T10:00:00Z",
                        "store_id": "S1",
                        "register_id": register,
                        "amount": amount,
                        "lines": [{"item_code": item, "quantity": "1", "unit_price": amount}],
                    }
                    for name, item, register, amount in samples
                ]
                result = await ingest(db, "POS", "remote-clearing", records)
                assert result["quarantined"] == 0 and result["canonical_created"] == 3
                await db.commit()
                await retotal_store_day(
                    "S1", "2026-10-01", db, {"user": "finance", "role": "finance"}
                )
                expected = {"BLSPRDEXP": 10, "PROPSTEXP": 5, "SHIPAMZN": 7, "REMSLSCL": -22}
                rows = (
                    (
                        await db.execute(
                            select(AuditTotal).where(
                                AuditTotal.total_name.in_(
                                    [f"Configured: {name}" for name in expected]
                                )
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                assert {row.total_name: row.calculated_amount for row in rows} == {
                    f"Configured: {name}": Decimal(amount) for name, amount in expected.items()
                }
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_remote_clearing_contains_all_workbook_components_with_negative_sign():
    from apps.totals.combined import calculate_combined

    # The workbook abbreviates PAY_ADVPYBK as PAY_ADVPYB in its comment.
    workbook_names = {
        "BLSPRDEXP",
        "CRPCHKREQ",
        "CTSYMTRSL",
        "CRTSYSTPRE",
        "CTSYSTPSL",
        "CUSTGDWL",
        "DISP_SUP",
        "DONATCOLL",
        "DONATE",
        "FCLTYRPR",
        "GFTWRPSL",
        "HURCANDON",
        "INVENEXPE",
        "MISC_EXP",
        "MISC_INC",
        "OCOLLPLCC",
        "PAY_ADV",
        "PAY_ADVPYBK",
        "POSTAGE",
        "PROEXPPLCC",
        "PROPSTEXP",
        "SOSPSTEXP",
        "STRTRAVEL",
        "SHIPAMZN",
        "SHIPEBAY",
        "SHIPWLMT",
    }
    definitions = [AuditTotalDefinition(**total, rollups=[]) for total in CLIENT_TOTALS]
    by_id = {item.id: item for item in definitions}
    parent = next(item for item in definitions if item.name == "REMSLSCL")
    groups = [by_id[item] for item in parent.combined_total_ids]
    assert (
        set().union(*({by_id[item].name for item in group.combined_total_ids} for group in groups))
        == workbook_names
    )
    assert all(op == "-" for group in groups for op in group.combined_total_operators)
    values = {item.id: {} for item in definitions if item.calculation_basis == "Raw"}
    for item in definitions:
        if item.name in workbook_names:
            values[item.id] = {None: Decimal("1")}
    calculate_combined(definitions, values)
    assert values[parent.id] == {None: Decimal("-26")}
