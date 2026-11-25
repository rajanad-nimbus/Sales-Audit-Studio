"""Paid-in/out item mappings transcribed from the ReSA setup workbook."""

import asyncio
import json
from decimal import Decimal
from pathlib import Path

import pytest
from models import AuditTotal, AuditTotalDefinition, Base, FoundationReference, StoreDay
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.ingest.service import ingest
from apps.store_days.routes import retotal_store_day
from apps.totals.client_totals import CLIENT_TOTALS

ITEMS = json.loads((Path(__file__).parent / "fixtures/workbook_paid_items.json").read_text())


def test_all_transcribed_paid_component_item_lists_match_catalog():
    by_name = {total["name"]: total for total in CLIENT_TOTALS}
    assert len(ITEMS) == 28
    for name, expected in ITEMS.items():
        restrictions = [
            r
            for r in by_name[name]["restrictions"]
            if r["realm"] == "Line" and r["field"] == "item_code"
        ]
        assert len(restrictions) == 1, name
        assert restrictions[0]["operator"] == "in", name
        assert set(restrictions[0]["value"].split(",")) == set(expected), name


@pytest.mark.parametrize("name", sorted(ITEMS))
def test_paid_component_calculates_transcribed_item(name):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                area = 3000 if name == "POSTAGEXP" else 2000
                register = {"SHIPAMZN": "892", "SHIPEBAY": "895", "SHIPWLMT": "880"}.get(
                    name, "001"
                )
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
                record = {
                    "source_record_id": "sale",
                    "record_type": "Sale",
                    "event_time": "2026-10-01T10:00:00Z",
                    "store_id": "S1",
                    "register_id": register,
                    "amount": "10",
                    "lines": [{"item_code": ITEMS[name][0], "quantity": "1", "unit_price": "10"}],
                }
                result = await ingest(db, "POS", "item", [record])
                assert result["quarantined"] == 0 and result["canonical_created"] == 1
                await db.commit()
                await retotal_store_day(
                    "S1", "2026-10-01", db, {"user": "finance", "role": "finance"}
                )
                total = (
                    await db.execute(
                        select(AuditTotal).where(AuditTotal.total_name == f"Configured: {name}")
                    )
                ).scalar_one()
                assert total.calculated_amount == Decimal("10")
        finally:
            await engine.dispose()

    asyncio.run(run())


COMPARISONS = json.loads(
    (Path(__file__).parent / "fixtures/workbook_paid_comparisons.json").read_text()
)


@pytest.mark.parametrize("name", sorted(COMPARISONS))
def test_paid_component_declared_total_and_difference(name):
    from apps.ingest.pos_formats.standard import StandardLog
    from apps.ingest.register_events import ingest_register_events

    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                area = 3000 if name == "POSTAGEXP" else 2000
                register = {"SHIPAMZN": "892", "SHIPEBAY": "895", "SHIPWLMT": "880"}.get(
                    name, "001"
                )
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
                record = {
                    "source_record_id": "sale",
                    "record_type": "Sale",
                    "event_time": "2026-10-01T10:00:00Z",
                    "store_id": "S1",
                    "register_id": register,
                    "amount": "10",
                    "lines": [{"item_code": ITEMS[name][0], "quantity": "1", "unit_price": "10"}],
                }
                assert (await ingest(db, "POS", "item", [record]))["quarantined"] == 0
                for number, increment, declared in [(1, "8", "8"), (2, "2", "10"), (3, "2", "12")]:
                    event = {
                        "source_record_id": f"total-{number}",
                        "store_id": "S1",
                        "business_date": "2026-10-01",
                        "register_id": "001",
                        "transaction_number": str(number),
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
                        "value": increment,
                        "lines": [],
                        "discounts": [],
                        "taxes": [],
                        "tenders": [],
                        "details": {
                            "user_references": [
                                {"field": 1, "value": COMPARISONS[name]["declared"]}
                            ]
                        },
                    }
                    log = StandardLog(
                        "rtlog", "S1", "2026-10-01", "2026-10-01T00:00:00Z", "POS", [event]
                    )
                    assert (await ingest_register_events(db, log))["created"] == 1
                    await db.commit()
                    await retotal_store_day(
                        "S1", "2026-10-01", db, {"user": "finance", "role": "finance"}
                    )
                    expected = {
                        name: Decimal("10"),
                        COMPARISONS[name]["declared"]: Decimal(declared),
                        COMPARISONS[name]["difference"]: Decimal("10") - Decimal(declared),
                    }
                    rows = (
                        (
                            await db.execute(
                                select(AuditTotal).where(
                                    AuditTotal.audit_version == number,
                                    AuditTotal.total_name.in_(
                                        [f"Configured: {key}" for key in expected]
                                    ),
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                    values = {
                        row.total_name.removeprefix("Configured: "): row.calculated_amount
                        for row in rows
                    }
                    assert values == expected, name
        finally:
            await engine.dispose()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("area", "stamp", "postage_expense"),
    [(1999, 0, 0), (2000, 9, 0), (2999, 9, 0), (3000, 0, 9), (3999, 0, 9), (4000, 0, 0)],
)
def test_paid_component_area_and_marketplace_register_restrictions(area, stamp, postage_expense):
    """Each of the three item kinds is sold once per local and remote register."""

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
                registers = ("001", "880", "881", "889", "893", "892", "897", "895", "896")
                records = [
                    {
                        "source_record_id": f"{register}-{item}",
                        "record_type": "Sale",
                        "event_time": "2026-10-01T10:00:00Z",
                        "store_id": "S1",
                        "register_id": register,
                        "amount": "1",
                        "lines": [{"item_code": item, "quantity": "1", "unit_price": "1"}],
                    }
                    for register in registers
                    for item in ("NM981BSI", "NM832BSI", "NM877BSI")
                ]
                result = await ingest(db, "POS", "area-register", records)
                assert result["quarantined"] == 0 and result["canonical_created"] == 27
                await db.commit()
                await retotal_store_day(
                    "S1", "2026-10-01", db, {"user": "finance", "role": "finance"}
                )
                expected = {
                    "CRTSYSTPRE": stamp,
                    "POSTAGEXP": postage_expense,
                    "GFTWRPSL": 3,
                    "POSTAGE": 3,
                    "SHIPAMZN": 4,
                    "SHIPEBAY": 4,
                    "SHIPWLMT": 4,
                }
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
                    f"Configured: {name}": Decimal(value)
                    for name, value in expected.items()
                    if value
                }
        finally:
            await engine.dispose()

    asyncio.run(run())


@pytest.mark.parametrize("name", sorted(COMPARISONS))
def test_paid_component_return_reduces_amount_and_difference(name):
    from apps.ingest.pos_formats.standard import StandardLog
    from apps.ingest.register_events import ingest_register_events

    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                area = 3000 if name == "POSTAGEXP" else 2000
                register = {"SHIPAMZN": "892", "SHIPEBAY": "895", "SHIPWLMT": "880"}.get(
                    name, "001"
                )
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
                records = [
                    {
                        "source_record_id": kind,
                        "record_type": kind,
                        "event_time": "2026-10-01T10:00:00Z",
                        "store_id": "S1",
                        "register_id": register,
                        "amount": amount,
                        "lines": [
                            {"item_code": ITEMS[name][0], "quantity": "1", "unit_price": amount}
                        ],
                    }
                    for kind, amount in (("Sale", "10"), ("Return", "3"))
                ]
                result = await ingest(db, "POS", "return", records)
                assert result["quarantined"] == 0 and result["canonical_created"] == 2
                event = {
                    "source_record_id": "total",
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
                    "value": "8",
                    "lines": [],
                    "discounts": [],
                    "taxes": [],
                    "tenders": [],
                    "details": {
                        "user_references": [{"field": 1, "value": COMPARISONS[name]["declared"]}]
                    },
                }
                log = StandardLog(
                    "rtlog", "S1", "2026-10-01", "2026-10-01T00:00:00Z", "POS", [event]
                )
                assert (await ingest_register_events(db, log))["created"] == 1
                await db.commit()
                await retotal_store_day(
                    "S1", "2026-10-01", db, {"user": "finance", "role": "finance"}
                )
                expected = {
                    name: Decimal("7"),
                    COMPARISONS[name]["declared"]: Decimal("8"),
                    COMPARISONS[name]["difference"]: Decimal("-1"),
                }
                rows = (
                    (
                        await db.execute(
                            select(AuditTotal).where(
                                AuditTotal.total_name.in_(
                                    [f"Configured: {key}" for key in expected]
                                )
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                assert {
                    row.total_name.removeprefix("Configured: "): row.calculated_amount
                    for row in rows
                } == expected
        finally:
            await engine.dispose()

    asyncio.run(run())


@pytest.mark.parametrize("name", sorted(COMPARISONS))
def test_paid_component_difference_rule_creates_one_closure_blocking_case(
    name, install_controls, monkeypatch
):
    from integrations.ontology.audit_controls import TotalRule
    from models import AuditEvent, Case, Recommendation
    from models import Exception as AuditException

    from apps.exports.eligibility import store_day_export_invalid
    from apps.ingest.pos_formats.standard import StandardLog
    from apps.ingest.register_events import ingest_register_events
    from apps.store_days.closures import close_blocker

    difference = COMPARISONS[name]["difference"]
    install_controls(
        total_rules=[
            TotalRule(
                id="difference",
                name="Difference",
                total_name=f"Configured: {difference}",
                balance_level="Store",
                comparison="ABS_GT",
                threshold=Decimal("0"),
                threshold_currency="USD",
                severity="High",
            )
        ]
    )

    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                area = 3000 if name == "POSTAGEXP" else 2000
                register = {"SHIPAMZN": "892", "SHIPEBAY": "895", "SHIPWLMT": "880"}.get(
                    name, "001"
                )
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
                record = {
                    "source_record_id": "sale",
                    "record_type": "Sale",
                    "event_time": "2026-10-01T10:00:00Z",
                    "store_id": "S1",
                    "register_id": register,
                    "amount": "10",
                    "lines": [{"item_code": ITEMS[name][0], "quantity": "1", "unit_price": "10"}],
                }
                assert (await ingest(db, "POS", "item", [record]))["quarantined"] == 0
                event = {
                    "source_record_id": "total",
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
                    "value": "8",
                    "lines": [],
                    "discounts": [],
                    "taxes": [],
                    "tenders": [],
                    "details": {
                        "user_references": [{"field": 1, "value": COMPARISONS[name]["declared"]}]
                    },
                }
                log = StandardLog(
                    "rtlog", "S1", "2026-10-01", "2026-10-01T00:00:00Z", "POS", [event]
                )
                assert (await ingest_register_events(db, log))["created"] == 1
                await db.commit()
                for _ in range(2):
                    await retotal_store_day(
                        "S1", "2026-10-01", db, {"user": "finance", "role": "finance"}
                    )
                cases = (
                    (
                        await db.execute(
                            select(Case).where(Case.case_type == "Rule breach: Difference")
                        )
                    )
                    .scalars()
                    .all()
                )
                assert len(cases) == 1 and cases[0].status == "Open"
                error = (
                    await db.execute(
                        select(AuditException).where(AuditException.case_id == cases[0].id)
                    )
                ).scalar_one()
                assert error.exception_type == "CONFIGURED_RULE_VARIANCE"
                assert error.exception_amount == Decimal("2") and error.close_impact == "Yes"
                assert "case(s) remain open" in await close_blocker(
                    db, await db.get(StoreDay, "day")
                )
                recommendation = Recommendation(
                    id="recommendation",
                    case_id=cases[0].id,
                    investigation_id="investigation",
                    disposition_type="Correction",
                    action_class="review",
                    expected_workflow="review",
                    financial_impact=Decimal("2"),
                    confidence="High",
                    status="Proposed",
                )
                db.add(recommendation)
                await db.commit()
                # Characterize the existing case after a later sale increases the difference.
                late = {
                    **record,
                    "source_record_id": "late",
                    "amount": "3",
                    "lines": [{"item_code": ITEMS[name][0], "quantity": "1", "unit_price": "3"}],
                }
                assert (await ingest(db, "POS", "late", [late]))["canonical_created"] == 1
                await db.commit()
                await retotal_store_day(
                    "S1", "2026-10-01", db, {"user": "finance", "role": "finance"}
                )
                current = (
                    await db.execute(
                        select(AuditTotal).where(
                            AuditTotal.audit_version == 3,
                            AuditTotal.total_name == f"Configured: {difference}",
                        )
                    )
                ).scalar_one()
                await db.refresh(cases[0])
                await db.refresh(error)
                await db.refresh(recommendation)
                assert current.calculated_amount == Decimal("5")
                assert error.exception_amount == Decimal("2")
                assert cases[0].total_exception_amount == Decimal("2")
                assert not cases[0].amount_needs_review
                assert recommendation.status == "Proposed"
                breach_events = (
                    (
                        await db.execute(
                            select(AuditEvent).where(
                                AuditEvent.case_id == cases[0].id,
                                AuditEvent.event_type == "CONFIGURED_RULE_BREACH",
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                assert len(breach_events) == 1
                assert await db.scalar(
                    select(store_day_export_invalid(db, "ERP", "S1", "2026-10-01"))
                )
                if name == "DONATE":
                    # Isolate the amount gate with synthetic complete evidence and a stub release refresh.
                    from apps.cases.records import create_decision, ontology_integration
                    from apps.cases.schemas import HumanDecisionCreate

                    async def refresh():
                        return set()

                    monkeypatch.setattr(ontology_integration, "refresh", refresh)
                    cases[0].evidence_completeness = 100
                    await db.commit()
                    decision = await create_decision(
                        HumanDecisionCreate(
                            case_id=cases[0].id,
                            recommendation_id=recommendation.id,
                            decision_type="Resolution",
                            decision="Approved",
                            rationale="Reviewed original evidence",
                        ),
                        db,
                        {"user": "finance", "role": "finance"},
                    )
                    assert decision.decision == "Approved"
                    await db.refresh(recommendation)
                    assert recommendation.status == "Approved"
        finally:
            await engine.dispose()

    asyncio.run(run())
