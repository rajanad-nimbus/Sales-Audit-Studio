"""Standard reports: scoped to the user's stores, paged on the server, and downloadable as CSV."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException, Response
from models import (
    AuditTotal,
    Base,
    CanonicalTransaction,
    Case,
    StoreDay,
    TransactionAdjustment,
    TransactionTax,
    VarianceTolerance,
)
from models import Exception as ExceptionRow
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.reports import routes as report_routes
from apps.reports.routes import list_reports, run_report

FINANCE = {"user": "finance", "role": "finance"}
NOW = datetime(2026, 10, 1, tzinfo=UTC)


async def run(name, start, end, fmt, db, user, page=1, page_size=50):
    """Call the endpoint as FastAPI does, keeping the X-Has-More header with a JSON body."""
    response = Response()
    result = await run_report(name, start, end, page, page_size, fmt, response, db, user)
    if fmt == "json":
        result["header_has_more"] = response.headers["X-Has-More"]
    return result


def run_with_db(scenario):
    async def go():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                await scenario(db)
        finally:
            await engine.dispose()

    asyncio.run(go())


def total(identifier, day, name, variance, version=1):
    return AuditTotal(
        id=identifier,
        store_day_id=day,
        audit_version=version,
        total_name=name,
        level="Store",
        calculated_amount=Decimal("100"),
        declared_amount=Decimal("100") - Decimal(variance),
        variance_amount=Decimal(variance),
    )


async def base(db):
    db.add_all(
        [
            StoreDay(id="a", store_id="S1", business_date="2026-10-01", audit_version=1),
            StoreDay(id="b", store_id="S2", business_date="2026-10-02", audit_version=1),
            total("t1", "a", "POS to GL Variance", "3"),
            total("t2", "a", "Processor to Bank Variance", "0"),
            total("t3", "b", "POS to GL Variance", "8"),
            VarianceTolerance(
                id="r", within_amount=Decimal("5"), case_amount=Decimal("20"), active=True
            ),
        ]
    )
    for identifier, status in (("c1", "Open"), ("c2", "Closed")):
        db.add(
            Case(
                id=identifier,
                case_number=identifier.upper(),
                case_type="X",
                business_date="2026-10-01",
                store_id="S1",
                status=status,
                currency="USD",
            )
        )
    await db.flush()
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


def test_variance_report_lists_only_variances_with_their_tolerance_outcome():
    async def scenario(db):
        await base(db)
        report = await run("variances", None, None, "json", db, FINANCE)
        assert [(r[0], r[2], r[7], r[9]) for r in report["rows"]] == [
            ("S2", "POS to GL Variance", Decimal("8"), "Needs reason"),
            ("S1", "POS to GL Variance", Decimal("3"), "Within tolerance"),
        ]
        ranged = await run("variances", "2026-10-02", None, "json", db, FINANCE)
        assert [r[0] for r in ranged["rows"]] == ["S2"]

    run_with_db(scenario)


def test_error_report_counts_open_errors_and_respects_store_scope():
    async def scenario(db):
        await base(db)
        [row] = (await run("errors", None, None, "json", db, FINANCE))["rows"]
        assert (row[0], row[1], row[4], row[5], row[6]) == (
            "S1",
            "UNMATCHED_SALE",
            2,
            1,
            Decimal("20"),
        )
        manager = {"user": "m", "role": "analyst", "store_scoped": True, "stores": ["S2"]}
        assert (await run("errors", None, None, "json", db, manager))["rows"] == []

    run_with_db(scenario)


def test_status_report_csv_download_listing_and_bad_input():
    async def scenario(db):
        await base(db)
        status = await run("store-days", None, None, "json", db, FINANCE)
        assert {r[0] for r in status["rows"]} == {"S1", "S2"}
        response = await run("variances", None, None, "csv", db, FINANCE)
        assert response.body.decode().splitlines()[0].startswith("Store,Business date,Total")
        assert "variances_start_now.csv" in response.headers["content-disposition"]
        assert response.headers["x-report-truncated"] == "false"

        listed = await list_reports(FINANCE)
        assert [r["name"] for r in listed] == [
            "variances",
            "errors",
            "store-days",
            "tax",
            "tax-corrections",
        ]
        assert all(r["title"] and r["description"] for r in listed)
        with pytest.raises(HTTPException) as unknown:
            await run("nope", None, None, "json", db, FINANCE)
        assert unknown.value.status_code == 404
        with pytest.raises(HTTPException) as bad_date:
            await run("errors", "2026-13-45", None, "json", db, FINANCE)
        assert bad_date.value.status_code == 422

    run_with_db(scenario)


def test_a_report_is_read_a_page_at_a_time_and_says_whether_another_follows():
    async def scenario(db):
        days = [f"2026-09-{n:02d}" for n in range(1, 26)]  # 25 store days
        db.add_all(
            StoreDay(id=f"d{n}", store_id="S1", business_date=day, audit_version=0)
            for n, day in enumerate(days)
        )
        await db.commit()

        first = await run("store-days", None, None, "json", db, FINANCE, page=1, page_size=10)
        second = await run("store-days", None, None, "json", db, FINANCE, page=2, page_size=10)
        last = await run("store-days", None, None, "json", db, FINANCE, page=3, page_size=10)

        assert [len(p["rows"]) for p in (first, second, last)] == [10, 10, 5]
        assert [p["has_more"] for p in (first, second, last)] == [True, True, False]
        assert [p["header_has_more"] for p in (first, second, last)] == ["true", "true", "false"]
        # No row repeats or is skipped between pages, newest day first.
        seen = [r[1] for p in (first, second, last) for r in p["rows"]]
        assert seen == sorted(days, reverse=True)
        assert (second["page"], second["page_size"]) == (2, 10)

        # Asking past the end is empty rather than an error.
        beyond = await run("store-days", None, None, "json", db, FINANCE, page=9, page_size=10)
        assert beyond["rows"] == [] and beyond["has_more"] is False

    run_with_db(scenario)


def test_each_page_does_only_one_pages_worth_of_database_work():
    async def scenario(db):
        db.add_all(
            StoreDay(id=f"d{n}", store_id="S1", business_date=f"2026-08-{n:02d}", audit_version=0)
            for n in range(1, 29)
        )
        await db.commit()
        seen = []
        original = report_routes.readiness

        async def spy(session, days, *args):
            seen.append(len(days))
            return await original(session, days, *args)

        report_routes.readiness = spy
        try:
            await run("store-days", None, None, "json", db, FINANCE, page=1, page_size=10)
        finally:
            report_routes.readiness = original
        assert seen == [10]  # readiness was worked out for the page, not for all 28 days

    run_with_db(scenario)


def test_a_csv_download_reads_every_page_and_says_when_it_stops_early(monkeypatch):
    async def scenario(db):
        db.add_all(
            StoreDay(id=f"d{n}", store_id="S1", business_date=f"2026-07-{n:02d}", audit_version=0)
            for n in range(1, 24)
        )
        await db.commit()
        monkeypatch.setattr(report_routes, "CSV_CHUNK", 5)
        everything = await run("store-days", None, None, "csv", db, FINANCE)
        assert len(everything.body.decode().splitlines()) == 1 + 23
        assert everything.headers["x-report-truncated"] == "false"

        monkeypatch.setattr(report_routes, "CSV_MAX_ROWS", 10)
        cut = await run("store-days", None, None, "csv", db, FINANCE)
        assert len(cut.body.decode().splitlines()) == 1 + 10
        assert cut.headers["x-report-truncated"] == "true"

    run_with_db(scenario)


async def add_tax(db):
    def transaction(identifier, store, amount):
        return CanonicalTransaction(
            id=identifier,
            business_date="2026-10-01",
            transaction_date=NOW,
            event_timestamp=NOW,
            processing_timestamp=NOW,
            settlement_date="2026-10-01",
            transaction_type="Sale",
            source_lineage="pos-map-v1",
            currency="USD",
            signed_amount=Decimal(amount),
            store_id=store,
            channel_id="store",
        )

    db.add_all(
        [
            transaction("x1", "S1", "110"),
            transaction("x2", "S1", "-55"),
            transaction("x3", "S2", "22"),
        ]
    )
    await db.flush()
    for identifier, tx, taxable, tax in (
        ("a", "x1", "100", "10"),
        ("b", "x2", "-50", "-5"),
        ("c", "x3", "20", "2"),
    ):
        db.add(
            TransactionTax(
                id=identifier,
                transaction_id=tx,
                tax_code="VAT",
                tax_name="VAT 10%",
                rate=Decimal("0.1"),
                taxable_amount=Decimal(taxable),
                tax_amount=Decimal(tax),
            )
        )
    for identifier, delta, status in (
        ("adj", "1.25", "Approved"),
        ("rej", "99", "Rejected"),
        ("bad", "abc", "Approved"),
    ):
        db.add(
            TransactionAdjustment(
                id=identifier,
                transaction_id="x1",
                adjustment_type="Tax",
                proposed_values={"tax_delta": delta},
                rationale=f"{identifier} reason",
                status=status,
                proposed_by="clerk",
                reviewed_by="approver" if status == "Approved" else None,
            )
        )
    await db.commit()


def test_tax_report_groups_by_code_and_nets_returns():
    async def scenario(db):
        await add_tax(db)
        rows = (await run("tax", None, None, "json", db, FINANCE))["rows"]
        by_store = {r[0]: r for r in rows}
        # Returns carry negative tax, so S1's net is 10 - 5 on a taxable base of 100 - 50.
        assert (by_store["S1"][6], by_store["S1"][7], by_store["S1"][8]) == (2, 50, 5)
        assert by_store["S2"][8] == 2
        scoped = {"user": "m", "role": "analyst", "store_scoped": True, "stores": ["S2"]}
        assert {r[0] for r in (await run("tax", None, None, "json", db, scoped))["rows"]} == {"S2"}

    run_with_db(scenario)


def test_tax_corrections_report_lists_only_approved_corrections_one_per_row():
    async def scenario(db):
        await add_tax(db)
        report = await run("tax-corrections", None, None, "json", db, FINANCE)
        by_reason = {r[8]: r for r in report["rows"]}
        assert set(by_reason) == {"adj reason", "bad reason"}  # the rejected one is not listed
        assert by_reason["adj reason"][4] == Decimal("1.25")
        assert by_reason["adj reason"][6] == "approver"
        assert by_reason["bad reason"][4] == "Invalid"
        scoped = {"user": "m", "role": "analyst", "store_scoped": True, "stores": ["S2"]}
        assert (await run("tax-corrections", None, None, "json", db, scoped))["rows"] == []
        earlier = (datetime.now(UTC) - timedelta(days=1)).date().isoformat()
        assert (await run("tax-corrections", earlier, None, "json", db, FINANCE))["rows"] == []

    run_with_db(scenario)
