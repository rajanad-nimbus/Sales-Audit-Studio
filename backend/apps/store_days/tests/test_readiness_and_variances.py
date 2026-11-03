"""Derived readiness, variance tolerances and explanations, and reopen and period guards."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from apps.audit.error_routes import patterns
from apps.ingest.service import ingest
from apps.store_days import variances
from apps.store_days.closures import ReopenRequest, close_store_day, reopen_store_day
from apps.store_days.periods import PeriodCloseBody, close_period
from apps.store_days.routes import get_store_day, list_store_days, store_days_needing_attention
from apps.store_days.variance_routes import ExplanationBody, ToleranceBody, create_tolerance
from apps.store_days.variance_routes import explain_variance as explain
from apps.transactions.models import CanonicalTransaction
from fastapi import HTTPException
from integrations.ontology.nimbus import audit
from models import (
    AuditTotal,
    Base,
    Case,
    ExportBatch,
    ExportDestination,
    ExportItem,
    FoundationReference,
    RegisterEvent,
    SourceRecord,
    StoreDay,
    StoreDayClosureRequest,
    VarianceTolerance,
)
from models import Exception as ExceptionRow
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

FINANCE = {"user": "finance.user"}
TODAY = datetime.now(UTC).date()
DATE = (TODAY - timedelta(days=10)).isoformat()


def run_with_db(scenario):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                await scenario(db)
        finally:
            await engine.dispose()

    asyncio.run(run())


def rule(total=None, trait=None, within="0", case="0", rule_id="r"):
    return VarianceTolerance(
        id=rule_id,
        total_name=total,
        location_trait=trait,
        within_amount=Decimal(within),
        case_amount=Decimal(case),
        active=True,
    )


def test_the_most_specific_tolerance_wins_and_sets_the_outcome():
    rules = [
        rule(within="1", case="50", rule_id="all"),
        rule(total="POS to GL Variance", within="5", case="20", rule_id="total"),
        rule(total="POS to GL Variance", trait="Outlet", within="10", case="100", rule_id="group"),
    ]
    outlet = variances.tolerance_for(rules, "POS to GL Variance", {"Outlet"})
    assert outlet.rule_id == "group"
    assert variances.tolerance_for(rules, "POS to GL Variance", set()).rule_id == "total"
    assert variances.tolerance_for(rules, "Other", {"Outlet"}).rule_id == "all"
    assert variances.tolerance_for([], "Other", set()) == variances.NO_TOLERANCE

    assert variances.outcome(Decimal("0"), outlet) == variances.BALANCED
    assert variances.outcome(Decimal("-10"), outlet) == variances.WITHIN
    assert variances.outcome(Decimal("10.01"), outlet) == variances.NEEDS_REASON
    assert variances.outcome(Decimal("100.01"), outlet) == variances.NEEDS_CASE
    # With no rule every cent needs a case, as before tolerances existed.
    assert variances.outcome(Decimal("0.01"), variances.NO_TOLERANCE) == variances.NEEDS_CASE


async def totaled_day(db, *, variance="3", status="Auditing", version=1) -> StoreDay:
    """A totaled store day whose POS to GL comparison carries a variance."""
    db.add_all(
        [
            FoundationReference(
                id="ref-S1",
                category="Store",
                code="S1",
                name="S1",
                active=True,
                attributes={"location_traits": ["Outlet"]},
            ),
            StoreDay(
                id="day",
                store_id="S1",
                business_date=DATE,
                status=status,
                audit_version=version,
                totals_stale=False,
            ),
        ]
    )
    for name, amount in (("Processor to Bank Variance", "0"), ("POS to GL Variance", variance)):
        db.add(
            AuditTotal(
                id=f"{name}-{version}",
                store_day_id="day",
                audit_version=version,
                total_name=name,
                level="Store",
                calculated_amount=Decimal("100"),
                declared_amount=Decimal("100") - Decimal(amount),
                variance_amount=Decimal(amount),
                currency="USD",
            )
        )
    await db.commit()
    return await db.get(StoreDay, "day")


async def request_closure(db):
    db.add(
        StoreDayClosureRequest(
            id="req", store_day_id="day", requested_by="auditor", evidence_basis="ok"
        )
    )
    await db.commit()


def test_a_variance_needing_a_reason_blocks_closure_until_it_is_explained():
    async def scenario(db):
        await totaled_day(db)
        await create_tolerance(
            ToleranceBody(total_name="POS to GL Variance", within_amount="1", case_amount="10"),
            db,
            FINANCE,
        )
        await request_closure(db)

        detail = await get_store_day("S1", DATE, None, db, FINANCE)
        row = next(t for t in detail["totals"] if t["name"] == "POS to GL Variance")
        assert row["outcome"] == "Needs reason" and not row["resolved"]
        assert "unresolved reconciliation variance" in detail["close_blockers"]
        assert detail["readiness"]["audit"]["unexplained_variances"] == 1

        with pytest.raises(HTTPException) as blocked:
            await close_store_day("S1", DATE, db, FINANCE)
        assert blocked.value.status_code == 422

        await explain(
            "S1", DATE, ExplanationBody(total_id=row["id"], reason="Rounding at GL"), db, FINANCE
        )
        closed = await close_store_day("S1", DATE, db, FINANCE)
        assert closed["status"] == "Closed"

        detail = await get_store_day("S1", DATE, None, db, FINANCE)
        row = next(t for t in detail["totals"] if t["name"] == "POS to GL Variance")
        assert row["resolved"] and row["explanation"]["reason"] == "Rounding at GL"

    run_with_db(scenario)


def test_a_variance_above_the_case_threshold_needs_a_case_or_correction():
    async def scenario(db):
        await totaled_day(db, variance="30")
        await create_tolerance(
            ToleranceBody(location_trait="Outlet", within_amount="1", case_amount="10"),
            db,
            FINANCE,
        )
        body = ExplanationBody(total_id="POS to GL Variance-1", reason="Missing deposit")
        with pytest.raises(HTTPException) as no_case:
            await explain("S1", DATE, body, db, FINANCE)
        assert no_case.value.status_code == 422

        db.add(
            Case(
                id="case-other",
                case_number="C-2",
                case_type="GL",
                business_date="2020-01-01",
                store_id="S1",
            )
        )
        await db.commit()
        with pytest.raises(HTTPException) as wrong_day:
            await explain(
                "S1", DATE, body.model_copy(update={"case_id": "case-other"}), db, FINANCE
            )
        assert wrong_day.value.status_code == 422

        db.add(
            Case(id="case", case_number="C-1", case_type="GL", business_date=DATE, store_id="S1")
        )
        await db.commit()
        made = await explain("S1", DATE, body.model_copy(update={"case_id": "case"}), db, FINANCE)
        assert made["outcome"] == "Needs case" and made["case_id"] == "case"

    run_with_db(scenario)


def test_an_explanation_survives_a_retotal_only_while_the_variance_is_unchanged():
    async def scenario(db):
        day = await totaled_day(db)
        db.add(rule(within="1", case="10"))
        await db.commit()
        await explain(
            "S1",
            DATE,
            ExplanationBody(total_id="POS to GL Variance-1", reason="Rounding"),
            db,
            FINANCE,
        )
        same = AuditTotal(
            id="same",
            store_day_id="day",
            audit_version=2,
            total_name="POS to GL Variance",
            level="Store",
            calculated_amount=Decimal("100"),
            variance_amount=Decimal("3"),
        )
        moved = AuditTotal(
            id="moved",
            store_day_id="day",
            audit_version=2,
            total_name="POS to GL Variance",
            level="Store",
            calculated_amount=Decimal("100"),
            variance_amount=Decimal("4"),
        )
        result = await variances.evaluate(db, day, [same, moved])
        assert result["same"]["resolved"] and not result["moved"]["resolved"]

    run_with_db(scenario)


def test_reopen_is_refused_in_a_closed_period_and_recorded_when_allowed():
    async def scenario(db):
        await totaled_day(db, variance="0", status="Closed")
        day = await db.get(StoreDay, "day")
        day.closed_by = "closer"
        await db.commit()

        period = await close_period(PeriodCloseBody(period_end=DATE), db, FINANCE)
        detail = await get_store_day("S1", DATE, None, db, FINANCE)
        assert detail["period_closed_through"] == DATE
        assert "closed through" in detail["reopen_blocked_by"][0]
        with pytest.raises(HTTPException) as refused:
            await reopen_store_day("S1", DATE, ReopenRequest(rationale="Late deposit"), db, FINANCE)
        assert refused.value.status_code == 409

        from apps.store_days.periods import PeriodReopenBody, reopen_period

        await reopen_period(period["id"], PeriodReopenBody(rationale="Audit fix"), db, FINANCE)
        await reopen_store_day("S1", DATE, ReopenRequest(rationale="Late deposit"), db, FINANCE)
        detail = await get_store_day("S1", DATE, None, db, FINANCE)
        [reopen] = detail["reopens"]
        assert reopen["reason"] == "Late deposit" and reopen["closed_by"] == "closer"
        assert reopen["reopened_by"] == "finance.user"

    run_with_db(scenario)


def test_a_period_cannot_close_while_store_days_in_it_are_open():
    async def scenario(db):
        await totaled_day(db, variance="0")
        with pytest.raises(HTTPException) as open_days:
            await close_period(PeriodCloseBody(period_end=DATE), db, FINANCE)
        assert open_days.value.status_code == 422
        assert "S1" in open_days.value.detail

    run_with_db(scenario)


async def export(db, *, acknowledged: bool):
    db.add_all(
        [
            ExportDestination(id="dest", name="ERP", kind="file", config={}),
            ExportBatch(
                id="batch",
                destination_id="dest",
                dataset="adjustments",
                status="Acknowledged" if acknowledged else "Sent",
                idempotency_key="k",
                created_by="x",
            ),
            ExportItem(
                id="item",
                batch_id="batch",
                destination_id="dest",
                dataset="adjustments",
                object_id="case",
                state="Delivered",
                store_id="S1",
                business_date=DATE,
            ),
        ]
    )
    await db.commit()


def test_a_store_day_with_final_exports_cannot_reopen():
    async def scenario(db):
        await totaled_day(db, variance="0", status="Closed")
        await export(db, acknowledged=True)
        with pytest.raises(HTTPException) as final:
            await reopen_store_day("S1", DATE, ReopenRequest(rationale="Fix"), db, FINANCE)
        assert final.value.status_code == 409 and "exports are final" in final.value.detail

    run_with_db(scenario)


def manifest(status: str) -> SourceRecord:
    stamp = datetime.fromisoformat(f"{DATE}T00:00:00+00:00")
    payload = {"store_id": "S1", "business_date": DATE, "declared_count": 1, "declared_total": 5}
    return SourceRecord(
        id="m",
        source_system="POSControl",
        source_version="v1",
        source_record_id="m",
        delivery_id="d",
        payload=json.dumps(payload).encode(),
        payload_hash="h",
        received_at=stamp,
        source_event_time=stamp,
        status=status,
    )


def sale(record_id: str, register: str = "R1", day: str = DATE) -> dict:
    return {
        "source_record_id": record_id,
        "record_type": "Sale",
        "event_time": f"{day}T10:00:00Z",
        "store_id": "S1",
        "register_id": register,
        "amount": "5",
    }


def test_readiness_derives_data_audit_and_export_views():
    async def scenario(db):
        await totaled_day(db, variance="0")
        [view] = await list_store_days(None, 100, db, FINANCE)
        readiness = view["readiness"]
        assert readiness["ingest"]["state"] == "No data"
        assert readiness["audit"]["state"] == "Clean"
        assert readiness["export"]["state"] == "Not exported"
        assert not readiness["ready_to_close"]

        await ingest(db, "POS", "d1", [sale("a")])
        day = await db.get(StoreDay, "day")
        day.totals_stale = False
        await db.commit()
        [view] = await list_store_days(None, 100, db, FINANCE)
        assert view["readiness"]["ingest"]["state"] == "No control total"

        db.add(manifest("Processed"))
        await db.commit()
        await export(db, acknowledged=False)
        [view] = await list_store_days(None, 100, db, FINANCE)
        assert view["readiness"]["ingest"]["state"] == "Complete"
        assert view["readiness"]["export"]["state"] == "Exported"
        assert view["readiness"]["ready_to_close"]

        detail = await get_store_day("S1", DATE, None, db, FINANCE)
        assert detail["data_quality"]["state"] == "Final"
        assert {t["data_quality"] for t in detail["totals"]} == {"Final"}

        db.add(
            Case(id="gap", case_number="C-9", case_type="Gap", business_date=DATE, store_id="S1")
        )
        db.add(
            ExceptionRow(
                id="e",
                case_id="gap",
                exception_type="INCOMPLETE_FEED",
                exception_family="Completeness",
                source_system="Reconciliation",
                detection_origin="Reconciliation",
                detection_rule_id="RECON-INCOMPLETE_FEED",
                exception_amount=Decimal("5"),
                estimated_exposure=Decimal("5"),
                severity="Medium",
                confidence="High",
                close_impact="Yes",
            )
        )
        await db.commit()
        detail = await get_store_day("S1", DATE, None, db, FINANCE)
        assert detail["readiness"]["ingest"]["state"] == "Incomplete"
        assert detail["readiness"]["audit"]["state"] == "Needs attention"
        assert detail["data_quality"]["state"] == "Partial"

    run_with_db(scenario)


def test_attention_lists_late_stores_and_stuck_store_days():
    async def scenario(db):
        await totaled_day(db, variance="0")
        db.add(
            FoundationReference(id="ref-S2", category="Store", code="S2", name="S2", active=True)
        )
        await db.commit()
        result = await store_days_needing_attention(DATE, 3, 200, db, FINANCE)
        assert {row["store_id"] for row in result["late_stores"]} == {"S1", "S2"}
        assert next(r for r in result["late_stores"] if r["store_id"] == "S1")["store_day_open"]
        [stuck] = result["stuck_store_days"]
        assert stuck["store_id"] == "S1" and stuck["days_open"] == 10
        assert stuck["holding_back"] == ["Data: No data"]

    run_with_db(scenario)


def test_patterns_flag_an_error_repeating_at_one_register():
    async def scenario(db):
        days = [(TODAY - timedelta(days=offset)).isoformat() for offset in (1, 2, 3)]
        for index, day in enumerate(days):
            await ingest(db, "POS", f"d{index}", [sale(f"t{index}", "R7", day)])
            transaction = (
                await db.execute(
                    select(CanonicalTransaction).where(CanonicalTransaction.business_date == day)
                )
            ).scalar_one()
            case_id = f"case-{index}"
            db.add(
                Case(
                    id=case_id,
                    case_number=f"C-{index}",
                    case_type="Unmatched",
                    business_date=day,
                    store_id="S1",
                )
            )
            db.add(
                ExceptionRow(
                    id=f"e-{index}",
                    case_id=case_id,
                    exception_type="UNMATCHED_SALE",
                    exception_family="Settlement",
                    source_system="Reconciliation",
                    detection_origin="Reconciliation",
                    detection_rule_id="RECON-UNMATCHED_SALE",
                    exception_amount=Decimal("5"),
                    estimated_exposure=Decimal("5"),
                    severity="Medium",
                    confidence="High",
                    close_impact="Yes",
                )
            )
            audit(
                db,
                "CASE_CREATED",
                case_id,
                "Case",
                case_id,
                "raised",
                after={"transaction_ids": [transaction.id]},
            )
        db.add(
            RegisterEvent(
                id="ev",
                source_record_id="ev",
                source_format="x",
                store_id="S1",
                business_date=days[0],
                register_id="R2",
                event_time=datetime.now(UTC),
                event_type="NoSale",
                transaction_number="1",
                audit_code="NOSALE_EXCESS",
                audited=True,
            )
        )
        await db.commit()

        result = await patterns(30, 3, None, db, FINANCE)
        top = result["registers"][0]
        assert (top["store_id"], top["register_id"], top["code"]) == ("S1", "R7", "UNMATCHED_SALE")
        assert top["days"] == 3 and top["repeating"]
        nosale = next(r for r in result["registers"] if r["register_id"] == "R2")
        assert nosale["days"] == 1 and not nosale["repeating"]
        assert result["store_summary"][0]["store_id"] == "S1"
        assert result["store_summary"][0]["repeating_codes"] == 1

    run_with_db(scenario)
