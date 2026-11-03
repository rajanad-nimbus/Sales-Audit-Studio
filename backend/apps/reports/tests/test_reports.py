"""Standard reports: scoped to the user's stores, bounded by date, and downloadable as CSV."""

import asyncio
from decimal import Decimal

import pytest
from fastapi import HTTPException
from models import AuditTotal, Base, Case, StoreDay, VarianceTolerance
from models import Exception as ExceptionRow
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.reports.routes import list_reports, run_report

FINANCE = {"user": "finance", "role": "finance"}


def run_with_db(scenario):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add_all(
                    [
                        StoreDay(
                            id="a", store_id="S1", business_date="2026-10-01", audit_version=1
                        ),
                        StoreDay(
                            id="b", store_id="S2", business_date="2026-10-02", audit_version=1
                        ),
                        AuditTotal(
                            id="t1",
                            store_day_id="a",
                            audit_version=1,
                            total_name="POS to GL Variance",
                            level="Store",
                            calculated_amount=Decimal("100"),
                            declared_amount=Decimal("97"),
                            variance_amount=Decimal("3"),
                        ),
                        AuditTotal(
                            id="t2",
                            store_day_id="a",
                            audit_version=1,
                            total_name="Processor to Bank Variance",
                            level="Store",
                            calculated_amount=Decimal("5"),
                            declared_amount=Decimal("5"),
                            variance_amount=Decimal("0"),
                        ),
                        AuditTotal(
                            id="t3",
                            store_day_id="b",
                            audit_version=1,
                            total_name="POS to GL Variance",
                            level="Store",
                            calculated_amount=Decimal("9"),
                            declared_amount=Decimal("1"),
                            variance_amount=Decimal("8"),
                        ),
                        VarianceTolerance(
                            id="r",
                            within_amount=Decimal("5"),
                            case_amount=Decimal("20"),
                            active=True,
                        ),
                        Case(
                            id="c1",
                            case_number="C1",
                            case_type="X",
                            business_date="2026-10-01",
                            store_id="S1",
                            status="Open",
                            currency="USD",
                        ),
                        Case(
                            id="c2",
                            case_number="C2",
                            case_type="X",
                            business_date="2026-10-01",
                            store_id="S1",
                            status="Closed",
                            currency="USD",
                        ),
                    ]
                )
                for index, case_id in enumerate(("c1", "c2")):
                    db.add(
                        ExceptionRow(
                            id=f"e{index}",
                            case_id=case_id,
                            exception_type="UNMATCHED_SALE",
                            exception_family="F",
                            source_system="x",
                            detection_origin="x",
                            detection_rule_id="x",
                            exception_amount=Decimal("10"),
                            estimated_exposure=Decimal("10"),
                            severity="Low",
                            confidence="High",
                            close_impact="No",
                        )
                    )
                await db.commit()
                await scenario(db)
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_variance_report_lists_only_variances_with_their_tolerance_outcome():
    async def scenario(db):
        report = await run_report("variances", None, None, "json", db, FINANCE)
        assert [(r[0], r[2], r[7], r[9]) for r in report["rows"]] == [
            ("S2", "POS to GL Variance", Decimal("8"), "Needs reason"),
            ("S1", "POS to GL Variance", Decimal("3"), "Within tolerance"),
        ]
        ranged = await run_report("variances", "2026-10-02", None, "json", db, FINANCE)
        assert [r[0] for r in ranged["rows"]] == ["S2"]

    run_with_db(scenario)


def test_error_report_counts_open_errors_and_respects_store_scope():
    async def scenario(db):
        report = await run_report("errors", None, None, "json", db, FINANCE)
        [row] = report["rows"]
        assert (row[0], row[1], row[4], row[5], row[6]) == (
            "S1",
            "UNMATCHED_SALE",
            2,
            1,
            Decimal("20"),
        )
        manager = {"user": "m", "role": "analyst", "store_scoped": True, "stores": ["S2"]}
        assert (await run_report("errors", None, None, "json", db, manager))["rows"] == []

    run_with_db(scenario)


def test_status_report_and_csv_download():
    async def scenario(db):
        status = await run_report("store-days", None, None, "json", db, FINANCE)
        assert {r[0] for r in status["rows"]} == {"S1", "S2"}
        response = await run_report("variances", None, None, "csv", db, FINANCE)
        text = response.body.decode()
        assert text.splitlines()[0].startswith("Store,Business date,Total")
        assert "variances_start_now.csv" in response.headers["content-disposition"]
        assert [r["name"] for r in await list_reports(FINANCE)] == [
            "variances",
            "errors",
            "store-days",
            "tax",
        ]
        with pytest.raises(HTTPException) as unknown:
            await run_report("nope", None, None, "json", db, FINANCE)
        assert unknown.value.status_code == 404
        with pytest.raises(HTTPException) as bad_date:
            await run_report("errors", "2026-13-45", None, "json", db, FINANCE)
        assert bad_date.value.status_code == 422

    run_with_db(scenario)


def test_tax_report_groups_by_code_and_lists_approved_corrections():
    from datetime import UTC, datetime

    from models import CanonicalTransaction, TransactionAdjustment, TransactionTax

    async def scenario(db):
        now = datetime(2026, 10, 1, tzinfo=UTC)

        def tx(identifier, store, amount):
            return CanonicalTransaction(
                id=identifier,
                business_date="2026-10-01",
                transaction_date=now,
                event_timestamp=now,
                processing_timestamp=now,
                settlement_date="2026-10-01",
                transaction_type="Sale",
                source_lineage="pos-map-v1",
                currency="USD",
                signed_amount=Decimal(amount),
                store_id=store,
                channel_id="store",
            )

        db.add_all([tx("x1", "S1", "110"), tx("x2", "S1", "-55"), tx("x3", "S2", "22")])
        await db.flush()
        for identifier, transaction, taxable, tax in (
            ("a", "x1", "100", "10"),
            ("b", "x2", "-50", "-5"),
            ("c", "x3", "20", "2"),
        ):
            db.add(
                TransactionTax(
                    id=identifier,
                    transaction_id=transaction,
                    tax_code="VAT",
                    tax_name="VAT 10%",
                    rate=Decimal("0.1"),
                    taxable_amount=Decimal(taxable),
                    tax_amount=Decimal(tax),
                )
            )
        db.add(
            TransactionAdjustment(
                id="adj",
                transaction_id="x1",
                adjustment_type="Tax",
                proposed_values={"tax_delta": "1.25"},
                rationale="r",
                status="Approved",
                proposed_by="p",
            )
        )
        db.add(
            TransactionAdjustment(
                id="rej",
                transaction_id="x1",
                adjustment_type="Tax",
                proposed_values={"tax_delta": "99"},
                rationale="r",
                status="Rejected",
                proposed_by="p",
            )
        )
        await db.commit()
        rows = (await run_report("tax", None, None, "json", db, FINANCE))["rows"]
        by_store = {r[0]: r for r in rows if r[2] == "VAT"}
        # Returns carry negative tax, so S1's net is 10 - 5 on a taxable base of 100 - 50.
        assert (by_store["S1"][6], by_store["S1"][7], by_store["S1"][8]) == (2, 50, 5)
        assert by_store["S2"][8] == 2
        [correction] = [r for r in rows if r[2] == "ADJ"]
        assert (correction[0], correction[6], correction[8]) == ("S1", 1, Decimal("1.25"))
        scoped = {"user": "m", "role": "analyst", "store_scoped": True, "stores": ["S2"]}
        assert {
            r[0] for r in (await run_report("tax", None, None, "json", db, scoped))["rows"]
        } == {"S2"}

    run_with_db(scenario)
