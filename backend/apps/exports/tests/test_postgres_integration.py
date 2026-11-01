"""Optional PostgreSQL integration test; provide ZETSA_TEST_POSTGRES_URL for an isolated test database."""

import asyncio
import os
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import main
import pytest
from fastapi import HTTPException
from models import (
    AuditEvent,
    AuditTotal,
    Base,
    CanonicalTransaction,
    Case,
    ConfiguredAuditRule,
    ExportDestination,
    HumanDecision,
    Recommendation,
    SourceRecord,
    StoreDay,
    TransactionAdjustment,
    ValidationObligation,
    Workflow,
)
from models import Exception as DBException
from schemas import CaseCreate
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.sql.dml import Update

from apps.corrections.routes import approve_adjustment, reject_adjustment
from apps.exports import routes
from apps.ingest import service as ingest
from apps.store_days.routes import retotal_store_day

POSTGRES_URL = os.getenv("ZETSA_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="ZETSA_TEST_POSTGRES_URL not configured")


async def verify_adjustment_export_and_case_audit(db):
    """Check scoped adjustment eligibility and case audit attribution."""
    destination = ExportDestination(id="d1", name="ERP", kind="file", format="json", config={})
    db.add(destination)
    db.add(
        Case(
            id="c1",
            case_number="C1",
            case_type="test",
            status="Closed",
            store_id="S1",
            business_date="2026-10-01",
            currency="USD",
            updated_at=datetime(2026, 10, 2, tzinfo=UTC),
        )
    )
    await db.flush()
    db.add(
        Recommendation(
            id="r1",
            case_id="c1",
            investigation_id="i",
            status="Approved",
            disposition_type="Correction",
            action_class="test",
            expected_workflow="test",
            financial_impact=Decimal("10"),
            confidence="High",
        )
    )
    await db.flush()
    db.add(
        HumanDecision(
            id="h1",
            case_id="c1",
            recommendation_id="r1",
            decision_type="human",
            decision="Approved",
            actor="approver",
            authority_check="Approved",
        )
    )
    db.add(
        Workflow(
            id="w1",
            case_id="c1",
            recommendation_id="r1",
            workflow_type="test",
            state="Completed",
            definition_version="1",
            current_step="Closed",
        )
    )
    await db.flush()
    db.add(
        ValidationObligation(
            id="v1",
            case_id="c1",
            workflow_id="w1",
            status="Complete",
            verification_result="Verified",
            expected_observation="test",
            due_window_hours=1,
            verification_rule="test",
        )
    )
    await db.commit()

    records, items, _, _ = await routes.build(db, destination, "adjustments", "backposts")
    assert [item[0] for item in items] == ["c1"]
    assert records[0]["approved_by"] == "approver"
    assert records[0]["backpost"] is True
    assert (await routes.build(db, destination, "adjustments", "current"))[0] == []
    created = await main.create_case(
        CaseCreate(case_type="test", business_date="2026-10-01", store_id="S1"),
        db,
        {"user": "finance", "role": "finance"},
    )
    audit = (
        await db.execute(select(AuditEvent).where(AuditEvent.case_id == created.id))
    ).scalar_one()
    assert audit.actor == "finance"


async def verify_correction_ingestion_and_retotal(db):
    """Check approved source lineage, configured totals, and quarantine behavior."""
    db.add(
        TransactionAdjustment(
            id="adjustment",
            adjustment_type="MissingTransaction",
            status="Proposed",
            proposed_values={
                "business_date": "2026-10-01",
                "store_id": "S2",
                "amount": "12.50",
                "transaction_type": "Sale",
            },
            rationale="verified",
            proposed_by="analyst",
        )
    )
    await db.commit()
    result = await approve_adjustment("adjustment", db, {"user": "finance"})
    transaction = await db.get(CanonicalTransaction, result["transaction_id"])
    assert (
        await db.get(SourceRecord, transaction.source_record_id)
    ).source_system == "ManualAdjustment"
    transaction.source_lineage = "pos-map-v1"
    db.add(StoreDay(id="day", store_id="S2", business_date="2026-10-01"))
    db.add(
        ConfiguredAuditRule(
            id="rule",
            name="POS limit",
            total_name="POS Net",
            threshold=Decimal("1"),
            severity="High",
        )
    )
    await db.commit()
    await retotal_store_day("S2", "2026-10-01", db, {"user": "finance", "role": "finance"})
    exception = (
        await db.execute(select(DBException).where(DBException.detection_rule_id == "rule"))
    ).scalar_one()
    assert (await db.get(Case, exception.case_id)).case_type == "Rule breach: POS limit"

    common = {
        "event_time": "2026-10-01T10:00:00Z",
        "record_type": "Sale",
        "amount": "1",
        "store_id": "S3",
    }
    stats = await ingest.ingest(
        db,
        "POS",
        "delivery",
        [
            {
                **common,
                "source_record_id": "bad",
                "lines": [{"item_code": "item", "quantity": "1000000000", "unit_price": "1"}],
            },
            {**common, "source_record_id": "good"},
        ],
    )
    await db.commit()
    assert stats["quarantined"] == 1 and stats["canonical_created"] == 1


async def seed_concurrency_records(db):
    db.add(
        TransactionAdjustment(
            id="race",
            adjustment_type="MissingTransaction",
            status="Proposed",
            proposed_values={
                "business_date": "2026-10-01",
                "store_id": "S4",
                "amount": "5",
                "transaction_type": "Sale",
            },
            rationale="verified",
            proposed_by="analyst",
        )
    )
    await db.commit()
    db.add(StoreDay(id="concurrent-day", store_id="S5", business_date="2026-10-01"))
    await db.commit()


async def verify_adjustment_review_race(session, monkeypatch):
    """Only one concurrent approval or rejection may claim the adjustment."""
    ready = asyncio.Event()
    arrivals = 0
    async with session() as first, session() as second:
        for concurrent_db in (first, second):
            original = concurrent_db.execute

            async def synchronized_execute(statement, *args, _original=original, **kwargs):
                nonlocal arrivals
                if isinstance(statement, Update):
                    arrivals += 1
                    if arrivals == 2:
                        ready.set()
                    await asyncio.wait_for(ready.wait(), timeout=5)
                return await _original(statement, *args, **kwargs)

            monkeypatch.setattr(concurrent_db, "execute", synchronized_execute)
        outcomes = await asyncio.gather(
            approve_adjustment("race", first, {"user": "finance1"}),
            reject_adjustment("race", second, {"user": "finance2"}),
            return_exceptions=True,
        )
    assert sum(isinstance(outcome, dict) for outcome in outcomes) == 1
    errors = [outcome for outcome in outcomes if isinstance(outcome, Exception)]
    assert (
        len(errors) == 1 and isinstance(errors[0], HTTPException) and errors[0].status_code == 409
    )


async def verify_retotal_lock_race(session, monkeypatch):
    """Concurrent retotals must serialize and produce distinct audit versions."""
    ready = asyncio.Event()
    arrivals = 0
    async with session() as first, session() as second:
        for concurrent_db in (first, second):
            original = concurrent_db.execute

            async def synchronized_lock(statement, *args, _original=original, **kwargs):
                nonlocal arrivals
                if getattr(statement, "_for_update_arg", None) is not None:
                    arrivals += 1
                    if arrivals == 2:
                        ready.set()
                    await asyncio.wait_for(ready.wait(), timeout=5)
                return await _original(statement, *args, **kwargs)

            monkeypatch.setattr(concurrent_db, "execute", synchronized_lock)
        results = await asyncio.gather(
            retotal_store_day("S5", "2026-10-01", first, {"user": "finance1", "role": "finance"}),
            retotal_store_day("S5", "2026-10-01", second, {"user": "finance2", "role": "finance"}),
        )
    assert {result["store_day"]["audit_version"] for result in results} == {1, 2}
    async with session() as db:
        versions = (
            (
                await db.execute(
                    select(AuditTotal.audit_version)
                    .where(AuditTotal.store_day_id == "concurrent-day")
                    .distinct()
                )
            )
            .scalars()
            .all()
        )
    assert set(versions) == {1, 2}


def test_postgres_scoped_adjustment_selection(monkeypatch):
    async def run():
        schema = "zetsa_test_" + uuid.uuid4().hex
        admin = create_async_engine(POSTGRES_URL)
        engine = None
        try:
            async with admin.begin() as connection:
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            engine = create_async_engine(
                POSTGRES_URL, connect_args={"server_settings": {"search_path": schema}}
            )
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session = async_sessionmaker(engine, expire_on_commit=False)
            async with session() as db:
                await verify_adjustment_export_and_case_audit(db)
                await verify_correction_ingestion_and_retotal(db)
                await seed_concurrency_records(db)
            await verify_adjustment_review_race(session, monkeypatch)
            await verify_retotal_lock_race(session, monkeypatch)
        finally:
            if engine is not None:
                await engine.dispose()
            async with admin.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await admin.dispose()

    asyncio.run(run())
