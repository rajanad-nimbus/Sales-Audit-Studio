"""Scenario 1 (settlement timing difference) through the whole case lifecycle:
investigate, human decision and policy gate, workflow, fresh-evidence validation, closure."""

import asyncio
from datetime import timedelta
from decimal import Decimal

import pytest
from apps.cases.records import create_decision
from apps.cases.schemas import HumanDecisionCreate
from apps.cases.workflows import execute_workflow, investigate_case, run_validation
from fastapi import HTTPException
from models import (
    Base,
    CanonicalTransaction,
    Case,
    PolicyEvaluation,
    Recommendation,
    SourceRecord,
    ValidationObligation,
    Workflow,
    utcnow,
)
from models import Exception as DBException
from schemas import ValidationRunRequest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

FINANCE = {"user": "finance@example.test", "role": "finance"}


@pytest.fixture(autouse=True)
def no_live_model(monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
        monkeypatch.delenv(name, raising=False)  # the deterministic investigation, never a live model


def processor_refund(n: int, received_at):
    record = SourceRecord(
        id=f"sr-{n}", source_system="Processor", source_version="v1", source_record_id=f"raw-{n}",
        delivery_id=f"d-{n}", payload=b"{}", payload_hash="0" * 64,
        received_at=received_at, source_event_time=received_at,
    )
    transaction = CanonicalTransaction(
        id=f"tx-{n}", business_date="2026-10-01", transaction_date=received_at,
        event_timestamp=received_at, processing_timestamp=received_at, settlement_date="2026-10-02",
        transaction_type="Refund", source_lineage="processor-map-v1", currency="USD",
        signed_amount=Decimal("-100.00"), store_id="S1", channel_id="Store", source_record_id=f"sr-{n}",
    )
    return record, transaction


def run(scenario):
    async def go():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                return await scenario(db)
        finally:
            await engine.dispose()

    return asyncio.run(go())


async def seed_case(db, amount: str, *, with_evidence: bool = True):
    if with_evidence:
        record, transaction = processor_refund(1, utcnow() - timedelta(hours=2))
        db.add_all([record, transaction])
    db.add(
        Case(
            id="c", case_number="C-1", case_type="Timing Difference", business_date="2026-10-01",
            store_id="S1", status="Open", currency="USD", total_exception_amount=Decimal(amount),
        )
    )
    await db.flush()
    db.add(
        DBException(
            id="e", case_id="c", exception_type="TIMING_DIFFERENCE", exception_family="Settlement",
            source_system="Processor", detection_origin="rule", detection_rule_id="r",
            exception_amount=Decimal(amount), estimated_exposure=Decimal("0"), severity="Low",
            confidence="High", close_impact="None",
        )
    )
    await db.commit()


@pytest.mark.parametrize(("amount", "policy"), [("100", "Permit"), ("250", "Permit"), ("550", "RequireHuman")])
def test_a_timing_difference_runs_from_investigation_to_closure(amount, policy):
    """The $250 autonomy limit changes the policy outcome, not whether a finance approval is needed."""

    async def scenario(db):
        await seed_case(db, amount)
        recommendation = await investigate_case("c", db, FINANCE)
        case = await db.get(Case, "c")
        assert (recommendation.disposition_type, recommendation.action_class) == ("Timing", "Disposition Only")
        assert recommendation.financial_impact == 0
        assert case.status == "In Review" and case.evidence_completeness == 100
        assert (await db.execute(select(PolicyEvaluation))).scalars().all() == []  # decided later

        decision = await create_decision(
            HumanDecisionCreate(
                case_id="c", recommendation_id=recommendation.id, decision_type="Finance",
                decision="Approved", rationale="Processor settled the refund a day late; timing only.",
            ),
            db,
            FINANCE,
        )
        evaluations = (await db.execute(select(PolicyEvaluation))).scalars().all()
        assert [e.outcome for e in evaluations] == [policy] and decision.authority_check == policy
        assert (await db.get(Case, "c")).status == "Resolving"
        [workflow] = (await db.execute(select(Workflow))).scalars().all()
        assert (workflow.workflow_type, workflow.state) == ("Timing", "Pending")

        executed = await execute_workflow(workflow.id, db, FINANCE)
        assert executed["state"] == "Executed"
        assert (await db.get(Case, "c")).status == "Pending Validation"

        # Validation accepts only evidence received after the action, in the case's scope.
        record, transaction = processor_refund(2, utcnow() + timedelta(seconds=5))
        db.add_all([record, transaction])
        await db.commit()
        [obligation] = (await db.execute(select(ValidationObligation))).scalars().all()
        result = await run_validation(
            obligation.id,
            db,
            ValidationRunRequest(
                source_record_ids=["sr-2"], rationale="Settlement arrived in the next processor file."
            ),
            FINANCE,
        )
        assert result["verification_result"] == "Verified" and result["case_status"] == "Closed"
        closed = await db.get(Case, "c")
        assert (closed.status, closed.investigation_status) == ("Closed", "Resolved")

    run(scenario)


def test_validation_with_evidence_from_before_the_action_does_not_close_the_case():
    async def scenario(db):
        await seed_case(db, "100")
        recommendation = await investigate_case("c", db, FINANCE)
        await create_decision(
            HumanDecisionCreate(
                case_id="c", recommendation_id=recommendation.id, decision_type="Finance",
                decision="Approved", rationale="Processor settled the refund a day late; timing only.",
            ),
            db,
            FINANCE,
        )
        [workflow] = (await db.execute(select(Workflow))).scalars().all()
        await execute_workflow(workflow.id, db, FINANCE)
        [obligation] = (await db.execute(select(ValidationObligation))).scalars().all()
        with pytest.raises(HTTPException) as refused:
            await run_validation(
                obligation.id,
                db,
                ValidationRunRequest(
                    source_record_ids=["sr-1"], rationale="Reusing the evidence the case opened with."
                ),
                FINANCE,
            )
        assert refused.value.status_code in (409, 422)
        assert (await db.get(Case, "c")).status == "Pending Validation"

    run(scenario)


def test_with_no_evidence_on_file_the_case_requests_it_and_waits():
    async def scenario(db):
        await seed_case(db, "100", with_evidence=False)
        recommendation = await investigate_case("c", db, FINANCE)
        case = await db.get(Case, "c")
        assert isinstance(recommendation, Recommendation)
        assert recommendation.disposition_type == "Request Evidence"
        assert case.status == "Open" and case.evidence_completeness == 0
        assert (await db.execute(select(PolicyEvaluation))).scalars().all() == []

    run(scenario)


def test_investigating_a_case_without_exceptions_is_refused():
    async def scenario(db):
        db.add(Case(id="x", case_number="C-2", case_type="T", business_date="2026-10-01",
                    store_id="S1", status="Open"))
        await db.commit()
        with pytest.raises(HTTPException) as refused:
            await investigate_case("x", db, FINANCE)
        assert refused.value.status_code == 409

    run(scenario)
