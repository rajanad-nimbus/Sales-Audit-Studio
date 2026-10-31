"""Bulk investigations load their exception evidence with one grouped query."""
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.agents import action_center, bulk_actions
from models import (
    Base,
    Case,
    Exception as DBException,
    Recommendation,
    ValidationObligation,
    Workflow,
)


def test_bulk_investigation_fetches_exceptions_once_and_groups_them_by_case(monkeypatch):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                for case_id in ("case-1", "case-2", "case-3"):
                    db.add(Case(
                        id=case_id,
                        case_number=case_id.upper(),
                        case_type="test",
                        business_date="2026-10-01",
                        store_id="S1",
                    ))
                    db.add(DBException(
                        id=f"exception-{case_id}",
                        case_id=case_id,
                        exception_type="UNMATCHED_SALE",
                        exception_family="Reconciliation",
                        source_system="POS",
                        detection_origin="Test",
                        detection_rule_id="TEST-UNMATCHED-SALE",
                        exception_amount=Decimal("10.00"),
                        estimated_exposure=Decimal("10.00"),
                        severity="Medium",
                        confidence="High",
                        close_impact="Yes",
                    ))
                await db.commit()

                received = []

                async def fake_investigation(_db, case, exceptions, **_kwargs):
                    received.append((case.id, [exception.id for exception in exceptions]))

                monkeypatch.setattr(bulk_actions.investigation_service, "run_investigation", fake_investigation)
                exception_queries = 0

                def count_exception_queries(_connection, _cursor, statement, _parameters, _context, _executemany):
                    nonlocal exception_queries
                    if "FROM EXCEPTIONS" in statement.upper():
                        exception_queries += 1

                event.listen(engine.sync_engine, "before_cursor_execute", count_exception_queries)
                try:
                    result = await action_center.bulk(
                        action_center.BulkRequest(action="investigate", ids=["case-1", "case-2", "case-3"]),
                        db,
                        {"user": "finance", "role": "finance"},
                    )
                finally:
                    event.remove(engine.sync_engine, "before_cursor_execute", count_exception_queries)

                assert result["ok"] == 3
                assert exception_queries == 1
                assert received == [
                    ("case-1", ["exception-case-1"]),
                    ("case-2", ["exception-case-2"]),
                    ("case-3", ["exception-case-3"]),
                ]
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_bulk_approval_batches_recommendations_and_refreshes_ontology_once(monkeypatch):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                for index in (1, 2, 3):
                    case_id = f"case-{index}"
                    db.add(Case(
                        id=case_id,
                        case_number=case_id.upper(),
                        case_type="test",
                        business_date="2026-10-01",
                        store_id="S1",
                        status="In Review",
                        currency="USD",
                        evidence_completeness=100,
                    ))
                    db.add(Recommendation(
                        id=f"recommendation-{index}",
                        case_id=case_id,
                        investigation_id=f"investigation-{index}",
                        disposition_type="Correction",
                        action_class="test",
                        expected_workflow="review",
                        financial_impact=Decimal("0"),
                        confidence="High",
                    ))
                await db.commit()

                refresh_calls = 0

                async def refresh_ontology():
                    nonlocal refresh_calls
                    refresh_calls += 1
                    return frozenset()

                monkeypatch.setattr(bulk_actions.ontology_integration, "refresh", refresh_ontology)
                monkeypatch.setattr(action_center.nimbus, "start_workflow", lambda *_args: None)
                recommendation_queries = 0

                def count_recommendation_queries(_connection, _cursor, statement, _parameters, _context, _executemany):
                    nonlocal recommendation_queries
                    if "FROM RECOMMENDATIONS" in statement.upper():
                        recommendation_queries += 1

                event.listen(engine.sync_engine, "before_cursor_execute", count_recommendation_queries)
                try:
                    result = await action_center.bulk(
                        action_center.BulkRequest(
                            action="approve", ids=["case-1", "case-2", "case-3"],
                        ),
                        db,
                        {"user": "finance", "role": "finance"},
                    )
                finally:
                    event.remove(engine.sync_engine, "before_cursor_execute", count_recommendation_queries)

                assert result["ok"] == 3
                assert recommendation_queries == 1
                assert refresh_calls == 1
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_bulk_advance_batches_workflows_and_selects_oldest_pending_records(monkeypatch):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                case_states = {
                    "resolving-1": "Resolving",
                    "resolving-2": "Resolving",
                    "validating-1": "Pending Validation",
                    "validating-2": "Pending Validation",
                }
                for case_id, status in case_states.items():
                    db.add(Case(
                        id=case_id,
                        case_number=case_id.upper(),
                        case_type="test",
                        business_date="2026-10-01",
                        store_id="S1",
                        status=status,
                    ))
                await db.flush()

                for case_id in case_states:
                    recommendation_id = f"rec-{case_id}"
                    db.add(Recommendation(
                        id=recommendation_id,
                        case_id=case_id,
                        investigation_id=f"inv-{case_id}",
                        disposition_type="Correction",
                        action_class="test",
                        expected_workflow="review",
                        financial_impact=Decimal("0"),
                        confidence="High",
                    ))
                    await db.flush()
                    workflow_ids = {
                        "resolving-1": ["wf-r1-z", "wf-r1-a"],
                        "resolving-2": ["wf-r2"],
                        "validating-1": ["wf-v1-a", "wf-v1-z"],
                        "validating-2": ["wf-v2"],
                    }[case_id]
                    created_at = datetime(2026, 10, 1, tzinfo=timezone.utc)
                    for workflow_index, workflow_id in enumerate(workflow_ids):
                        db.add(Workflow(
                            id=workflow_id,
                            case_id=case_id,
                            recommendation_id=recommendation_id,
                            workflow_type="Correction",
                            state="Pending",
                            definition_version="v1",
                            current_step="Awaiting action",
                            created_at=created_at + timedelta(seconds=workflow_index),
                        ))
                    if case_id.startswith("validating"):
                        for workflow_id in workflow_ids:
                            obligation_id = workflow_id.replace("wf-", "ob-")
                            db.add(ValidationObligation(
                                id=obligation_id,
                                case_id=case_id,
                                workflow_id=workflow_id,
                                expected_observation="Updated source record",
                                due_window_hours=24,
                                verification_rule="Fresh source evidence",
                                created_at=created_at + timedelta(seconds=workflow_ids.index(workflow_id)),
                            ))
                await db.commit()

                resolved = []
                validated = []

                def fake_resolve(_db, case, workflow):
                    resolved.append((case.id, workflow.id))

                def fake_validate(_db, case, workflow, obligation, evidence):
                    validated.append((case.id, workflow.id, obligation.id, evidence))

                monkeypatch.setattr(action_center.nimbus, "resolve", fake_resolve)
                monkeypatch.setattr(action_center.nimbus, "validate", fake_validate)
                workflow_queries = 0
                obligation_queries = 0
                bounded_child_queries = 0

                def count_transition_queries(_connection, _cursor, statement, _parameters, _context, _executemany):
                    nonlocal workflow_queries, obligation_queries, bounded_child_queries
                    normalized = statement.upper()
                    workflow_queries += "FROM WORKFLOWS" in normalized
                    obligation_queries += "FROM VALIDATION_OBLIGATIONS" in normalized
                    bounded_child_queries += "ROW_NUMBER() OVER" in normalized

                event.listen(engine.sync_engine, "before_cursor_execute", count_transition_queries)
                try:
                    result = await action_center.bulk(
                        action_center.BulkRequest(
                            action="advance", ids=list(case_states),
                        ),
                        db,
                        {"user": "finance", "role": "finance"},
                    )
                finally:
                    event.remove(engine.sync_engine, "before_cursor_execute", count_transition_queries)

                assert result["ok"] == 2 and result["skipped_count"] == 2
                assert workflow_queries == 2 and obligation_queries == 1
                assert bounded_child_queries == 2
                assert resolved == [("resolving-1", "wf-r1-z"), ("resolving-2", "wf-r2")]
                assert validated == [
                    ("validating-1", "wf-v1-a", "ob-v1-a", []),
                    ("validating-2", "wf-v2", "ob-v2", []),
                ]
        finally:
            await engine.dispose()

    asyncio.run(run())
