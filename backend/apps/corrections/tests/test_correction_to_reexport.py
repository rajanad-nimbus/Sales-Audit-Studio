"""A correction after a store day is totalled makes the day stale, so it must be re-totalled."""

import asyncio
from decimal import Decimal

from models import Base, CanonicalTransaction, Case, TransactionAdjustment
from models import Exception as ExceptionRow
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.cases.routes_lifecycle import review_case_amounts
from apps.cases.schemas import CaseAmountReview, CaseAmountReviewItem
from apps.corrections.routes import approve_adjustment
from apps.exports.selection import classify_backpost
from apps.ingest import reconciliation, service
from apps.store_days.models import StoreDay
from apps.store_days.routes import open_store_day_record, retotal_store_day

FINANCE = {"role": "finance", "user": "finance", "name": "Finance"}


def test_approved_correction_makes_the_totalled_day_stale_and_flags_the_case():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                common = {
                    "event_time": "2026-10-01T10:00:00Z",
                    "store_id": "S1",
                    "payment_reference": "REF",
                }
                await service.ingest(
                    db,
                    "POS",
                    "pos",
                    [{**common, "source_record_id": "pos", "record_type": "Sale", "amount": "100"}],
                )
                await service.ingest(
                    db,
                    "Processor",
                    "processor",
                    [
                        {
                            **common,
                            "source_record_id": "cap",
                            "record_type": "Capture",
                            "amount": "90",
                        }
                    ],
                )
                await reconciliation.reconcile(db, "2026-10-01")
                await open_store_day_record(db, "S1", "2026-10-01", "analyst")
                await db.commit()
                await retotal_store_day("S1", "2026-10-01", db, FINANCE)
                day = (await db.execute(select(StoreDay))).scalar_one()
                assert day.audit_version > 0 and not day.totals_stale

                capture = (
                    await db.execute(
                        select(CanonicalTransaction).where(
                            CanonicalTransaction.transaction_type == "Capture"
                        )
                    )
                ).scalar_one()
                db.add(
                    TransactionAdjustment(
                        id="fix",
                        transaction_id=capture.id,
                        adjustment_type="Amount",
                        proposed_values={"amount_delta": "10"},
                        status="Proposed",
                        rationale="verified",
                        proposed_by="analyst",
                    )
                )
                await db.commit()
                assert (await approve_adjustment("fix", db, FINANCE))["status"] == "Approved"

                await db.refresh(day)
                assert day.totals_stale, "the correction must force a re-total"
                assert (await db.execute(select(Case))).scalar_one().amount_needs_review
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_a_record_added_to_an_exported_day_is_flagged_as_a_late_addition():
    prior = {("S1", "2026-10-01"): ["batch-1"]}
    flag, reason, batches = classify_backpost("S1", "2026-10-01", "2026-10-03", prior)
    assert flag and reason == "Late addition to a business day already exported"
    assert batches == ["batch-1"]


def test_one_chain_from_import_to_correction_to_retotal_to_delivery(tmp_path, monkeypatch):
    """Import, reconcile, total, correct, re-total, then deliver the verified adjustment."""
    from models import (
        ExportDestination,
        HumanDecision,
        Recommendation,
        ValidationObligation,
        Workflow,
    )

    from apps.exports import routes as export_routes
    from apps.exports.schemas import RunIn

    monkeypatch.setattr(export_routes, "EXPORT_DIR", tmp_path)

    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                common = {
                    "event_time": "2026-10-01T10:00:00Z",
                    "store_id": "S1",
                    "payment_reference": "REF",
                }
                await service.ingest(
                    db,
                    "POS",
                    "pos",
                    [{**common, "source_record_id": "pos", "record_type": "Sale", "amount": "100"}],
                )
                await service.ingest(
                    db,
                    "Processor",
                    "processor",
                    [
                        {
                            **common,
                            "source_record_id": "cap",
                            "record_type": "Capture",
                            "amount": "90",
                        }
                    ],
                )
                await reconciliation.reconcile(db, "2026-10-01")
                await open_store_day_record(db, "S1", "2026-10-01", "analyst")
                await db.commit()
                await retotal_store_day("S1", "2026-10-01", db, FINANCE)
                day = (await db.execute(select(StoreDay))).scalar_one()
                first_version = day.audit_version

                capture = (
                    await db.execute(
                        select(CanonicalTransaction).where(
                            CanonicalTransaction.transaction_type == "Capture"
                        )
                    )
                ).scalar_one()
                db.add(
                    TransactionAdjustment(
                        id="fix",
                        transaction_id=capture.id,
                        adjustment_type="Amount",
                        proposed_values={"amount_delta": "10"},
                        status="Proposed",
                        rationale="verified",
                        proposed_by="analyst",
                    )
                )
                await db.commit()
                await approve_adjustment("fix", db, FINANCE)
                await db.refresh(day)
                assert day.totals_stale

                await retotal_store_day("S1", "2026-10-01", db, FINANCE)
                await db.refresh(day)
                assert not day.totals_stale and day.audit_version == first_version + 1

                # The correction changed the evidence, so the case amounts must be reviewed first.
                case = (await db.execute(select(Case))).scalar_one()
                assert case.amount_needs_review and case.status != "Closed"
                found = (
                    (await db.execute(select(ExceptionRow).where(ExceptionRow.case_id == case.id)))
                    .scalars()
                    .all()
                )
                await review_case_amounts(
                    case.id,
                    CaseAmountReview(
                        rationale="Reviewed after the approved correction",
                        exceptions=[
                            CaseAmountReviewItem(
                                exception_id=e.id,
                                amount=Decimal("10.00"),
                                exposure=Decimal("10.00"),
                            )
                            for e in found
                        ],
                    ),
                    db,
                    FINANCE,
                )
                assert not case.amount_needs_review
                case.status = "Closed"
                db.add_all(
                    [
                        ExportDestination(
                            id="d1", name="ERP", kind="file", format="json", config={}
                        ),
                        Recommendation(
                            id="r1",
                            case_id=case.id,
                            investigation_id="i",
                            status="Approved",
                            disposition_type="Correction",
                            action_class="test",
                            expected_workflow="test",
                            financial_impact=Decimal("10"),
                            confidence="High",
                        ),
                        HumanDecision(
                            id="h1",
                            case_id=case.id,
                            recommendation_id="r1",
                            decision_type="human",
                            decision="Approved",
                            actor="finance",
                            authority_check="Approved",
                        ),
                        Workflow(
                            id="w1",
                            case_id=case.id,
                            recommendation_id="r1",
                            workflow_type="test",
                            state="Completed",
                            definition_version="1",
                            current_step="Closed",
                        ),
                        ValidationObligation(
                            id="v1",
                            case_id=case.id,
                            workflow_id="w1",
                            status="Complete",
                            verification_result="Verified",
                            expected_observation="test",
                            due_window_hours=1,
                            verification_rule="test",
                        ),
                    ]
                )
                await db.commit()
                sent = await export_routes.run_export(
                    RunIn(destination_id="d1", dataset="adjustments"),
                    db,
                    {"role": "it", "user": "it"},
                )
                assert sent["status"] in ("Sent", "Acknowledged"), sent
                assert any("S1" in f.read_text() for f in tmp_path.rglob("*.json"))
        finally:
            await engine.dispose()

    asyncio.run(run())
