"""Scenario 4 (return missing tender evidence) through the case lifecycle.

A return of $94.22 exists but the processor refund and the bank posting have not been seen."""

from datetime import timedelta
from decimal import Decimal

import pytest
from apps.cases.records import create_decision
from apps.cases.schemas import HumanDecisionCreate
from apps.cases.tests.test_timing_narrative_lifecycle import (  # noqa: F401
    FINANCE,
    no_live_model,
    run,
)
from apps.cases.workflows import investigate_case
from fastapi import HTTPException
from integrations.ontology import nimbus
from models import CanonicalTransaction, Case, PolicyEvaluation, SourceRecord, Workflow, utcnow
from models import Exception as DBException
from sqlalchemy import select

AMOUNT = Decimal("94.22")


def source(db, sid, system, kind, at):
    db.add(
        SourceRecord(
            id=sid, source_system=system, source_version="v1", source_record_id=f"raw-{sid}",
            delivery_id=f"d-{sid}", payload=b"{}", payload_hash="0" * 64,
            received_at=at, source_event_time=at,
        )
    )
    db.add(
        CanonicalTransaction(
            id=f"tx-{sid}", business_date="2026-10-01", transaction_date=at, event_timestamp=at,
            processing_timestamp=at, settlement_date="2026-10-01", transaction_type=kind,
            source_lineage="pos-map-v1", currency="USD", signed_amount=-AMOUNT,
            store_id="S1", channel_id="Store", source_record_id=sid,
        )
    )


async def seed(db):
    source(db, "pos1", "POS", "Return", utcnow() - timedelta(hours=2))  # only the return is on file
    db.add(
        Case(
            id="c", case_number="C-1", case_type="Missing Refund", business_date="2026-10-01",
            store_id="S1", status="Open", currency="USD", total_exception_amount=AMOUNT,
        )
    )
    await db.flush()
    db.add(
        DBException(
            id="e", case_id="c", exception_type="MISSING_REFUND", exception_family="Settlement",
            source_system="POS", detection_origin="rule", detection_rule_id="r",
            exception_amount=AMOUNT, estimated_exposure=AMOUNT, severity="Medium",
            confidence="High", close_impact="None",
        )
    )
    await db.commit()


def approval(recommendation_id):
    return HumanDecisionCreate(
        case_id="c", recommendation_id=recommendation_id, decision_type="Finance",
        decision="Approved", rationale="Approving to test what the policy gate does.",
    )


async def provide_the_missing_evidence(db):
    at = utcnow() - timedelta(hours=1)
    source(db, "proc1", "Processor", "Refund", at)
    source(db, "bank1", "Bank", "Deposit", at)
    await db.commit()


def test_with_only_the_return_on_file_the_case_waits_for_evidence():
    async def scenario(db):
        await seed(db)
        recommendation = await investigate_case("c", db, FINANCE)
        case = await db.get(Case, "c")
        assert recommendation.disposition_type == "Request Evidence"
        assert case.status == "Open" and case.investigation_status == "Awaiting Evidence"
        assert 0 < case.evidence_completeness < 100  # the POS return is one of the three sources

    run(scenario)


def test_the_policy_gate_refuses_an_approval_while_the_required_evidence_is_incomplete():
    async def scenario(db):
        await seed(db)
        recommendation = await investigate_case("c", db, FINANCE)
        with pytest.raises(HTTPException) as refused:
            await create_decision(approval(recommendation.id), db, FINANCE)
        assert refused.value.status_code == 422 and "evidence incomplete" in refused.value.detail
        assert (await db.execute(select(Workflow))).scalars().all() == []  # nothing was dispatched

    run(scenario)


def test_complete_evidence_hands_a_collect_class_to_a_person_with_no_resolution_proposed():
    async def scenario(db):
        await seed(db)
        await investigate_case("c", db, FINANCE)
        await provide_the_missing_evidence(db)
        recommendation = await investigate_case("c", db, FINANCE)
        case = await db.get(Case, "c")
        assert (recommendation.disposition_type, recommendation.action_class) == (
            "Escalation", "Manual Review",
        )
        assert recommendation.financial_impact == AMOUNT
        assert case.status == "In Review" and case.evidence_completeness == 100
        assert case.investigation_status == "Ready for Decision"

    run(scenario)


def test_if_the_ontology_lets_the_agent_propose_it_recommends_the_recovery(monkeypatch):
    monkeypatch.setattr(nimbus, "agent_level", lambda code: "propose")

    async def scenario(db):
        await seed(db)
        await provide_the_missing_evidence(db)
        recommendation = await investigate_case("c", db, FINANCE)
        assert recommendation.disposition_type == "Recovery"
        assert recommendation.action_class == "Source Reprocess"
        # Above the $1,000 autonomy limit or not, recovering money is never permitted without a person.
        decision = await create_decision(approval(recommendation.id), db, FINANCE)
        [evaluation] = (await db.execute(select(PolicyEvaluation))).scalars().all()
        assert evaluation.outcome == "RequireHuman" and decision.authority_check == "RequireHuman"

    run(scenario)
