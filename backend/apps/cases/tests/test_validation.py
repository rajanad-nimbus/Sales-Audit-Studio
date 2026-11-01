import asyncio
from datetime import timedelta
from decimal import Decimal

import pytest
from apps.cases.workflows import run_validation, validation_candidates
from fastapi import HTTPException
from models import (
    AuditEvent,
    Base,
    CanonicalTransaction,
    Case,
    EvidenceSnapshot,
    SourceRecord,
    ValidationObligation,
    Workflow,
    utcnow,
)
from models import (
    Exception as DBException,
)
from pydantic import ValidationError
from schemas import ValidationRunRequest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


def transaction(record_id: str, store_id: str, received_at, transaction_id: str):
    event_at = received_at - timedelta(minutes=1)
    source = SourceRecord(
        id=record_id,
        source_system="POS",
        source_version="test-v1",
        source_record_id=f"raw-{record_id}",
        delivery_id=f"delivery-{record_id}",
        payload=b"{}",
        payload_hash="0" * 64,
        received_at=received_at,
        source_event_time=event_at,
    )
    canonical = CanonicalTransaction(
        id=transaction_id,
        business_date="2026-10-01",
        transaction_date=event_at,
        event_timestamp=event_at,
        processing_timestamp=received_at,
        settlement_date="2026-10-01",
        transaction_type="Sale",
        source_lineage="pos-map-v1",
        currency="USD",
        signed_amount=Decimal("10.00"),
        store_id=store_id,
        channel_id="Store",
        source_record_id=record_id,
    )
    return source, canonical


def test_validation_requires_fresh_in_scope_evidence_and_reviewer_rationale():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                executed_at = utcnow() - timedelta(minutes=30)
                old_source, old_transaction = transaction(
                    "old-source", "S1", executed_at - timedelta(minutes=1), "old-transaction"
                )
                new_source, new_transaction = transaction(
                    "new-source", "S1", utcnow(), "new-transaction"
                )
                foreign_source, foreign_transaction = transaction(
                    "foreign-source", "S2", utcnow(), "foreign-transaction"
                )
                case = Case(
                    id="case-1",
                    case_number="CASE-1",
                    case_type="Test",
                    business_date="2026-10-01",
                    store_id="S1",
                    status="Pending Validation",
                    investigation_status="In Progress",
                )
                workflow = Workflow(
                    id="workflow-1",
                    case_id=case.id,
                    recommendation_id="recommendation-1",
                    workflow_type="Correction",
                    state="Executed",
                    definition_version="v1",
                    current_step="Awaiting validation",
                )
                obligation = ValidationObligation(
                    id="validation-1",
                    case_id=case.id,
                    workflow_id=workflow.id,
                    expected_observation="Correction visible downstream",
                    due_window_hours=24,
                    verification_rule="Fresh evidence matches promised result",
                    created_at=executed_at,
                )
                exception = DBException(
                    id="exception-1",
                    case_id=case.id,
                    exception_type="TEST",
                    exception_family="Test",
                    source_system="POS",
                    detection_origin="Test",
                    detection_rule_id="TEST",
                    exception_amount=Decimal("10"),
                    estimated_exposure=Decimal("10"),
                    severity="Low",
                    confidence="High",
                    close_impact="No",
                )
                db.add_all(
                    [
                        case,
                        workflow,
                        obligation,
                        exception,
                        old_source,
                        old_transaction,
                        new_source,
                        new_transaction,
                        foreign_source,
                        foreign_transaction,
                    ]
                )
                await db.commit()

                page = await validation_candidates("validation-1", db, {"user": "finance"})
                assert [row["source_record_id"] for row in page["items"]] == ["new-source"]
                assert not page["has_more"]

                stale_request = ValidationRunRequest(
                    source_record_ids=["old-source"], rationale="This is a reviewed outcome"
                )
                with pytest.raises(HTTPException) as stale_error:
                    await run_validation("validation-1", db, stale_request, {"user": "finance"})
                assert stale_error.value.status_code == 422
                assert case.status == "Pending Validation"

                foreign_request = ValidationRunRequest(
                    source_record_ids=["foreign-source"], rationale="This is a reviewed outcome"
                )
                with pytest.raises(HTTPException) as foreign_error:
                    await run_validation("validation-1", db, foreign_request, {"user": "finance"})
                assert foreign_error.value.status_code == 422
                assert case.status == "Pending Validation"

                valid_request = ValidationRunRequest(
                    source_record_ids=["new-source"],
                    rationale="The new POS record confirms the approved correction was applied.",
                )
                result = await run_validation(
                    "validation-1", db, valid_request, {"user": "finance"}
                )
                assert result["verification_result"] == "Verified"
                assert case.status == "Closed"
                snapshot = (await db.execute(select(EvidenceSnapshot))).scalar_one()
                assert snapshot.evidence_data["source_record_ids"] == ["new-source"]
                assert "approved correction" in snapshot.evidence_data["rationale"]
                event = (
                    await db.execute(
                        select(AuditEvent).where(AuditEvent.event_type == "VALIDATION_VERIFIED")
                    )
                ).scalar_one()
                assert event.actor == "finance"
                assert event.after_state["source_record_ids"] == ["new-source"]
                assert "approved correction" in event.after_state["rationale"]
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_validation_request_rejects_empty_sources_and_blank_rationale():
    with pytest.raises(ValidationError):
        ValidationRunRequest(source_record_ids=[], rationale="Valid rationale text")
    with pytest.raises(ValidationError):
        ValidationRunRequest(source_record_ids=["source-1"], rationale="          ")
