import asyncio
from decimal import Decimal

import pytest
from apps.controls.rules.models import ConfiguredAuditRule
from apps.ingest import service
from apps.store_days import routes
from apps.totals.routes import preview_definition
from fastapi import HTTPException
from models import (
    AuditTotal,
    AuditTotalDefinition,
    Base,
    CanonicalTransaction,
    StoreDay,
    TransactionAdjustment,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


def test_retotal_sums_tax_details_by_register_and_preserves_return_sign():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session = async_sessionmaker(engine, expire_on_commit=False)
            async with session() as db:
                db.add(StoreDay(id="day", store_id="S1", business_date="2026-10-01"))
                db.add(
                    AuditTotalDefinition(
                        id="tax",
                        name="Tax by register",
                        measure="Tax",
                        aggregation="Sum",
                        source_system="POS",
                        level="Register",
                        active=True,
                    )
                )
                common = {"event_time": "2026-10-01T10:00:00Z", "store_id": "S1"}
                await service.ingest(
                    db,
                    "POS",
                    "d1",
                    [
                        {
                            **common,
                            "source_record_id": "sale",
                            "record_type": "Sale",
                            "amount": "100",
                            "register_id": "R1",
                            "payment_reference": "tax-sale",
                            "taxes": [
                                {"tax_code": "A", "tax_amount": "8"},
                                {"tax_code": "B", "tax_amount": "5"},
                            ],
                        },
                        {
                            **common,
                            "source_record_id": "return",
                            "record_type": "Return",
                            "amount": "20",
                            "register_id": "R1",
                            "taxes": [{"tax_code": "A", "tax_amount": "2.60"}],
                        },
                        {
                            **common,
                            "source_record_id": "other",
                            "record_type": "Sale",
                            "amount": "30",
                            "register_id": "R2",
                            "taxes": [{"tax_code": "A", "tax_amount": "3"}],
                        },
                        {
                            **common,
                            "source_record_id": "no-tax",
                            "record_type": "Sale",
                            "amount": "10",
                            "register_id": "R3",
                        },
                    ],
                )
                sale = (
                    await db.execute(
                        select(CanonicalTransaction).where(
                            CanonicalTransaction.payment_reference == "tax-sale"
                        )
                    )
                ).scalar_one()
                db.add_all(
                    [
                        TransactionAdjustment(
                            id="approved-tax",
                            transaction_id=sale.id,
                            adjustment_type="Tax",
                            proposed_values={"tax_delta": "2.00"},
                            status="Approved",
                            rationale="Finance correction",
                            proposed_by="finance",
                        ),
                        TransactionAdjustment(
                            id="proposed-tax",
                            transaction_id=sale.id,
                            adjustment_type="Tax",
                            proposed_values={"tax_delta": "100.00"},
                            status="Proposed",
                            rationale="Pending review",
                            proposed_by="finance",
                        ),
                    ]
                )
                await db.commit()
                await routes.retotal_store_day(
                    "S1", "2026-10-01", db, {"user": "finance", "role": "finance"}
                )
                totals = (
                    (
                        await db.execute(
                            select(AuditTotal).where(
                                AuditTotal.store_day_id == "day",
                                AuditTotal.total_name == "Configured: Tax by register",
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                assert {row.dimension_key: row.calculated_amount for row in totals} == {
                    "R1": Decimal("12.40"),
                    "R2": Decimal("3.00"),
                    "R3": Decimal("0.00"),
                }
        finally:
            await engine.dispose()

    asyncio.run(run())


@pytest.mark.parametrize("options", [{"calculation_basis": "Combined"}])
def test_unsupported_calculation_does_not_create_a_totals_version(options):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                day = StoreDay(id="day", store_id="S1", business_date="2026-10-01")
                db.add_all(
                    [
                        day,
                        AuditTotalDefinition(
                            id="unsupported", name="Unsupported", active=True, **options
                        ),
                    ]
                )
                await db.commit()
                with pytest.raises(HTTPException) as exc:
                    await routes.retotal_store_day("S1", "2026-10-01", db, {"user": "finance"})
                assert exc.value.status_code == 422
                assert day.audit_version == 0
                assert not (await db.execute(select(AuditTotal))).scalars().all()
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_cashier_totals_use_ingested_cashier_and_reject_missing_identity():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                day = StoreDay(id="day", store_id="S1", business_date="2026-10-01")
                db.add_all(
                    [
                        day,
                        AuditTotalDefinition(
                            id="cashier",
                            name="Cashier sales",
                            level="Cashier",
                            source_system="POS",
                            active=True,
                        ),
                    ]
                )
                common = {
                    "event_time": "2026-10-01T10:00:00Z",
                    "store_id": "S1",
                    "record_type": "Sale",
                }
                result = await service.ingest(
                    db,
                    "POS",
                    "delivery",
                    [
                        {**common, "source_record_id": "a", "amount": "10", "cashier_id": "C1"},
                        {**common, "source_record_id": "b", "amount": "20", "cashier_id": "C2"},
                        {**common, "source_record_id": "c", "amount": "3", "cashier_id": "C1"},
                        {**common, "source_record_id": "invalid", "amount": "1", "cashier_id": " "},
                    ],
                )
                assert result["quarantined"] == 1
                await db.commit()
                await routes.retotal_store_day("S1", "2026-10-01", db, {"user": "finance"})
                totals = (
                    (
                        await db.execute(
                            select(AuditTotal).where(
                                AuditTotal.total_name == "Configured: Cashier sales"
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                assert {item.dimension_key: item.calculated_amount for item in totals} == {
                    "C1": Decimal("13"),
                    "C2": Decimal("20"),
                }
                await service.ingest(
                    db, "POS", "later", [{**common, "source_record_id": "missing", "amount": "4"}]
                )
                await db.commit()
                with pytest.raises(HTTPException, match="cashier IDs"):
                    await routes.retotal_store_day("S1", "2026-10-01", db, {"user": "finance"})
                assert day.audit_version == 1
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_retotal_calculates_combined_totals_per_register():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add_all(
                    [
                        StoreDay(id="day", store_id="S1", business_date="2026-10-01"),
                        AuditTotalDefinition(
                            id="sales",
                            name="Sales",
                            level="Register",
                            source_system="POS",
                            transaction_types=["Sale"],
                            active=True,
                        ),
                        AuditTotalDefinition(
                            id="returns",
                            name="Returns",
                            level="Register",
                            source_system="POS",
                            transaction_types=["Return"],
                            active=True,
                        ),
                        AuditTotalDefinition(
                            id="net",
                            name="Net",
                            level="Register",
                            calculation_basis="Combined",
                            combined_total_ids=["sales", "returns"],
                            combined_operator="+",
                            active=True,
                        ),
                    ]
                )
                common = {
                    "event_time": "2026-10-01T10:00:00Z",
                    "store_id": "S1",
                    "register_id": "R1",
                }
                await service.ingest(
                    db,
                    "POS",
                    "delivery",
                    [
                        {
                            **common,
                            "source_record_id": "sale",
                            "amount": "20",
                            "record_type": "Sale",
                        },
                        {
                            **common,
                            "source_record_id": "return",
                            "amount": "3",
                            "record_type": "Return",
                        },
                    ],
                )
                await db.commit()
                preview = await preview_definition(
                    "net", "S1", "2026-10-01", db, {"user": "finance"}
                )
                assert preview["value"] == Decimal("17")
                assert preview["dimensions"] == [{"dimension": "R1", "value": Decimal("17")}]
                assert preview["record_count"] is None
                await routes.retotal_store_day("S1", "2026-10-01", db, {"user": "finance"})
                total = (
                    await db.execute(
                        select(AuditTotal).where(AuditTotal.total_name == "Configured: Net")
                    )
                ).scalar_one()
                assert (total.dimension_key, total.calculated_amount) == ("R1", Decimal("17"))
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_retotal_uses_latest_effective_revision_and_respects_deactivation():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session = async_sessionmaker(engine, expire_on_commit=False)
            async with session() as db:
                db.add(StoreDay(id="day", store_id="S1", business_date="2026-10-01"))
                db.add_all(
                    [
                        AuditTotalDefinition(
                            id="v1", name="Revised", revision=1, aggregation="Sum", active=True
                        ),
                        AuditTotalDefinition(
                            id="v2", name="Revised", revision=2, aggregation="Count", active=True
                        ),
                        AuditTotalDefinition(
                            id="v3",
                            name="Revised",
                            revision=3,
                            aggregation="Sum",
                            active=True,
                            start_date="2026-11-01",
                        ),
                        AuditTotalDefinition(id="d1", name="Disabled", revision=1, active=True),
                        AuditTotalDefinition(id="d2", name="Disabled", revision=2, active=False),
                        AuditTotalDefinition(
                            id="expired",
                            name="Expired",
                            revision=1,
                            active=True,
                            end_date="2026-09-30",
                        ),
                    ]
                )
                await service.ingest(
                    db,
                    "POS",
                    "d1",
                    [
                        {
                            "source_record_id": "a",
                            "event_time": "2026-10-01T10:00:00Z",
                            "record_type": "Sale",
                            "amount": "10",
                            "store_id": "S1",
                        },
                        {
                            "source_record_id": "b",
                            "event_time": "2026-10-01T11:00:00Z",
                            "record_type": "Sale",
                            "amount": "20",
                            "store_id": "S1",
                        },
                    ],
                )
                await db.commit()
                await routes.retotal_store_day(
                    "S1", "2026-10-01", db, {"user": "finance", "role": "finance"}
                )
                totals = (
                    (
                        await db.execute(
                            select(AuditTotal).where(
                                AuditTotal.store_day_id == "day",
                                AuditTotal.total_name.like("Configured:%"),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                assert {row.total_name: row.calculated_amount for row in totals} == {
                    "Configured: Revised": Decimal("2.00")
                }
                assert (totals[0].definition_id, totals[0].definition_revision) == ("v2", 2)
                assert routes.total_view(totals[0])["definition_revision"] == 2
                db.add(
                    AuditTotalDefinition(
                        id="v4", name="Revised", revision=4, aggregation="Sum", active=True
                    )
                )
                await db.commit()
                await routes.retotal_store_day(
                    "S1", "2026-10-01", db, {"user": "finance", "role": "finance"}
                )
                revisions = (
                    (
                        await db.execute(
                            select(AuditTotal)
                            .where(AuditTotal.total_name == "Configured: Revised")
                            .order_by(AuditTotal.audit_version)
                        )
                    )
                    .scalars()
                    .all()
                )
                assert [
                    (item.definition_id, item.definition_revision, item.calculated_amount)
                    for item in revisions
                ] == [("v2", 2, Decimal("2.00")), ("v4", 4, Decimal("30.00"))]
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_configured_rules_require_a_store_level_total():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add_all(
                    [
                        StoreDay(id="day", store_id="S1", business_date="2026-10-01"),
                        AuditTotalDefinition(
                            id="register-total",
                            name="Register Sales",
                            level="Register",
                            source_system="POS",
                            active=True,
                        ),
                        ConfiguredAuditRule(
                            id="register-rule",
                            name="Register limit",
                            total_name="Configured: Register Sales",
                            threshold=Decimal("10"),
                        ),
                    ]
                )
                await service.ingest(
                    db,
                    "POS",
                    "delivery",
                    [
                        {
                            "source_record_id": "sale",
                            "event_time": "2026-10-01T10:00:00Z",
                            "record_type": "Sale",
                            "amount": "11",
                            "store_id": "S1",
                            "register_id": "R1",
                        },
                    ],
                )
                await db.commit()

                with pytest.raises(HTTPException, match="requires a Store-level total") as exc:
                    await routes.retotal_store_day("S1", "2026-10-01", db, {"user": "finance"})

                assert exc.value.status_code == 422
                assert (await db.get(StoreDay, "day")).audit_version == 0
                assert not (await db.execute(select(AuditTotal))).scalars().all()
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_rule_target_validation_uses_effective_definition_without_transactions():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add_all(
                    [
                        StoreDay(id="day", store_id="S1", business_date="2026-10-01"),
                        AuditTotalDefinition(
                            id="register-total",
                            name="Register Sales",
                            level="Register",
                            source_system="POS",
                            active=True,
                        ),
                        ConfiguredAuditRule(
                            id="register-rule",
                            name="Register limit",
                            total_name="Configured: Register Sales",
                            threshold=Decimal("10"),
                        ),
                    ]
                )
                await db.commit()

                with pytest.raises(HTTPException, match="requires a Store-level total") as exc:
                    await routes.retotal_store_day("S1", "2026-10-01", db, {"user": "finance"})

                assert exc.value.status_code == 422
                assert (await db.get(StoreDay, "day")).audit_version == 0
                assert not (await db.execute(select(AuditTotal))).scalars().all()
        finally:
            await engine.dispose()

    asyncio.run(run())
