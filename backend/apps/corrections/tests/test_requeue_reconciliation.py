import asyncio
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from main import review_case_amounts
from models import (
    AuditEvent,
    Base,
    CanonicalTransaction,
    Case,
    Recommendation,
    TransactionAdjustment,
)
from models import Exception as DBException
from pydantic import ValidationError
from schemas import CaseAmountReview, CaseAmountReviewItem
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.agents.investigation_service import run_investigation
from apps.corrections.reconciliation import related_exception_ids, requeue_linked_exceptions
from apps.corrections.routes import approve_adjustment
from apps.ingest import reconciliation, service


def test_non_object_case_evidence_does_not_break_correction_link_lookup():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                transaction = CanonicalTransaction(
                    id="tx-malformed-evidence",
                    business_date="2026-10-01",
                    transaction_date=datetime.now(UTC),
                    event_timestamp=datetime.now(UTC),
                    processing_timestamp=datetime.now(UTC),
                    settlement_date="2026-10-01",
                    transaction_type="Sale",
                    source_lineage="pos-map-v1",
                    currency="USD",
                    signed_amount=10,
                    store_id="S1",
                    channel_id="store",
                    payment_reference="REF",
                    reconciliation_status="Exception",
                )
                case = Case(
                    id="case-malformed-evidence",
                    case_number="CASE-M",
                    case_type="Mismatch",
                    business_date="2026-10-01",
                    store_id="S1",
                )
                db.add_all([transaction, case])
                await db.flush()
                db.add(
                    AuditEvent(
                        id="malformed-evidence",
                        event_type="CASE_CREATED",
                        actor="system",
                        object_type="Case",
                        object_id=case.id,
                        case_id=case.id,
                        action_description="Legacy non-object evidence",
                        after_state=[transaction.id],
                    )
                )
                await db.commit()

                assert await related_exception_ids(db, transaction) == {transaction.id}
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_related_correction_pair_uses_database_lineage_for_large_cases():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                timestamp = datetime(2026, 10, 1, tzinfo=UTC)
                common = {
                    "business_date": "2026-10-01",
                    "transaction_date": timestamp,
                    "event_timestamp": timestamp,
                    "processing_timestamp": timestamp,
                    "settlement_date": "2026-10-01",
                    "currency": "USD",
                    "signed_amount": 10,
                    "store_id": "S1",
                    "channel_id": "store",
                    "payment_reference": "PAYMENT-1",
                    "reconciliation_status": "Exception",
                }
                target = CanonicalTransaction(
                    id="target", transaction_type="Sale", source_lineage="pos-map-v1", **common
                )
                counterpart = CanonicalTransaction(
                    id="counterpart",
                    transaction_type="Capture",
                    source_lineage="processor-map-v1",
                    **common,
                )
                case = Case(
                    id="large-case",
                    case_number="CASE-LARGE",
                    case_type="Amount mismatch",
                    business_date="2026-10-01",
                    store_id="S1",
                )
                transaction_ids = [
                    "target",
                    "counterpart",
                    *(f"unrelated-{i}" for i in range(1200)),
                ]
                db.add_all([target, counterpart, case])
                await db.flush()
                case_created = AuditEvent(
                    id="large-case-created",
                    event_type="CASE_CREATED",
                    actor="system",
                    object_type="Case",
                    object_id=case.id,
                    case_id=case.id,
                    action_description="Large reconciliation case",
                    after_state={"transaction_ids": transaction_ids},
                )
                db.add_all(
                    [
                        case_created,
                        AuditEvent(
                            id="large-case-follow-up",
                            event_type="CASE_CREATED",
                            actor="system",
                            object_type="Case",
                            object_id="child-case",
                            case_id="child-case",
                            action_description="Follow-up with malformed parent metadata",
                            after_state={
                                "parent_case_id": {"legacy": True},
                                "transaction_ids": ["target"],
                            },
                        ),
                    ]
                )
                db.add(
                    Case(
                        id="child-case",
                        case_number="CASE-CHILD",
                        case_type="Amount mismatch",
                        business_date="2026-10-01",
                        store_id="S1",
                    )
                )
                await db.flush()

                statements = []

                def capture_query(
                    _connection, _cursor, statement, _parameters, _context, _executemany
                ):
                    if "json_each" in statement.lower():
                        statements.append(statement)

                event.listen(engine.sync_engine, "before_cursor_execute", capture_query)
                try:
                    linked = await related_exception_ids(db, target)
                finally:
                    event.remove(engine.sync_engine, "before_cursor_execute", capture_query)

                assert linked == {"target", "counterpart"}
                assert len(statements) == 1
                assert " IN (" not in statements[0].upper()

                case_created.after_state = {"transaction_ids": ["target-suffix", "counterpart"]}
                await db.flush()
                assert await related_exception_ids(db, target) == {"target"}
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_correction_requeues_only_documented_pair_and_retains_original_case():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                common = {"event_time": "2026-10-01T10:00:00Z", "store_id": "S1"}
                for reference in ("A", "B"):
                    await service.ingest(
                        db,
                        "POS",
                        reference,
                        [
                            {
                                **common,
                                "source_record_id": f"pos-{reference}",
                                "record_type": "Sale",
                                "amount": "100",
                                "payment_reference": reference,
                            }
                        ],
                    )
                    await service.ingest(
                        db,
                        "Processor",
                        reference,
                        [
                            {
                                **common,
                                "source_record_id": f"proc-{reference}",
                                "record_type": "Capture",
                                "amount": "90",
                                "payment_reference": reference,
                            }
                        ],
                    )
                initial = await reconciliation.reconcile(db, "2026-10-01")
                assert initial["exceptions"]["AMOUNT_MISMATCH"] == 2
                await db.commit()
                rows = (await db.execute(select(CanonicalTransaction))).scalars().all()
                corrected = next(
                    row
                    for row in rows
                    if row.payment_reference == "A" and row.transaction_type == "Capture"
                )
                case = (await db.execute(select(Case))).scalar_one()
                case.status, case.investigation_status, case.evidence_completeness = (
                    "In Review",
                    "Ready for Decision",
                    100,
                )
                db.add(
                    Recommendation(
                        id="recommendation",
                        case_id=case.id,
                        investigation_id="i",
                        disposition_type="Timing",
                        action_class="test",
                        expected_workflow="test",
                        financial_impact=0,
                        confidence="High",
                        status="Proposed",
                    )
                )
                db.add(
                    TransactionAdjustment(
                        id="adjustment",
                        transaction_id=corrected.id,
                        adjustment_type="Amount",
                        proposed_values={"amount_delta": "10"},
                        status="Proposed",
                        rationale="verified",
                        proposed_by="analyst",
                    )
                )
                await db.commit()
                await approve_adjustment("adjustment", db, {"user": "finance"})
                assert (case.status, case.investigation_status, case.evidence_completeness) == (
                    "Open",
                    "Pending",
                    0,
                )
                assert case.amount_needs_review
                assert (await db.get(Recommendation, "recommendation")).status == "Superseded"
                assert (
                    await db.execute(
                        select(AuditEvent).where(AuditEvent.event_type == "CASE_EVIDENCE_STALE")
                    )
                ).scalar_one().case_id == case.id
                assert {
                    row.reconciliation_status for row in rows if row.payment_reference == "A"
                } == {"Unmatched"}
                assert {
                    row.reconciliation_status for row in rows if row.payment_reference == "B"
                } == {"Exception"}
                assert (
                    await db.execute(
                        select(AuditEvent).where(AuditEvent.event_type == "TRANSACTIONS_REQUEUED")
                    )
                ).scalar_one()
                result = await reconciliation.reconcile(db, "2026-10-01")
                assert result["matched_pairs"] == 1
                assert {
                    row.reconciliation_status for row in rows if row.payment_reference == "A"
                } == {"Matched"}
                assert case.status == "Open"
                exceptions = (
                    (await db.execute(select(DBException).where(DBException.case_id == case.id)))
                    .scalars()
                    .all()
                )
                with pytest.raises(HTTPException, match="require finance review"):
                    await run_investigation(db, case, exceptions, use_agent=False)
                with pytest.raises(ValidationError, match="at least 1 item"):
                    CaseAmountReview(rationale="reviewed", exceptions=[])
                with pytest.raises(ValidationError, match="no more than 2 decimal places"):
                    CaseAmountReview(
                        rationale="reviewed",
                        exceptions=[
                            CaseAmountReviewItem(
                                exception_id=exceptions[0].id, amount="10.001", exposure="10"
                            )
                        ],
                    )
                invalid = CaseAmountReview(
                    rationale="reviewed",
                    exceptions=[
                        CaseAmountReviewItem(
                            exception_id=exceptions[0].id, amount="10", exposure="10"
                        )
                    ],
                )
                with pytest.raises(HTTPException) as error:
                    await review_case_amounts(
                        case.id,
                        invalid,
                        db,
                        {
                            "user": "finance",
                            "role": "finance",
                            "store_scoped": True,
                            "stores": ["S2"],
                        },
                    )
                assert error.value.status_code == 403
                assert case.amount_needs_review and case.total_exception_amount == 20
                reviewed = await review_case_amounts(
                    case.id,
                    CaseAmountReview(
                        rationale="Only B remains unmatched",
                        exceptions=[
                            CaseAmountReviewItem(
                                exception_id=exceptions[0].id, amount="10", exposure="10"
                            )
                        ],
                    ),
                    db,
                    {"user": "finance", "role": "finance"},
                )
                assert not reviewed.amount_needs_review and reviewed.total_exception_amount == 10
                event = (
                    await db.execute(
                        select(AuditEvent).where(AuditEvent.event_type == "CASE_AMOUNTS_REVIEWED")
                    )
                ).scalar_one()
                assert event.after_state["before"]["amount"] == "20.00"
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_correction_waits_for_an_active_linked_workflow():
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
                    "one",
                    [{**common, "source_record_id": "pos", "record_type": "Sale", "amount": "100"}],
                )
                await service.ingest(
                    db,
                    "Processor",
                    "two",
                    [
                        {
                            **common,
                            "source_record_id": "processor",
                            "record_type": "Capture",
                            "amount": "90",
                        }
                    ],
                )
                await reconciliation.reconcile(db, "2026-10-01")
                case = (await db.execute(select(Case))).scalar_one()
                case.status = "Resolving"
                transaction = (
                    await db.execute(
                        select(CanonicalTransaction).where(
                            CanonicalTransaction.transaction_type == "Capture"
                        )
                    )
                ).scalar_one()
                db.add(
                    TransactionAdjustment(
                        id="correction",
                        transaction_id=transaction.id,
                        adjustment_type="Amount",
                        proposed_values={"amount_delta": "10"},
                        status="Proposed",
                        rationale="verified",
                        proposed_by="analyst",
                    )
                )
                await db.commit()
                with pytest.raises(HTTPException, match="active workflow") as exc:
                    await approve_adjustment("correction", db, {"user": "finance"})
                assert exc.value.status_code == 409
                assert (await db.get(TransactionAdjustment, "correction")).status == "Proposed"
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_header_correction_does_not_guess_how_a_split_tender_changed():
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
                    "one",
                    [
                        {
                            **common,
                            "source_record_id": "pos",
                            "record_type": "Sale",
                            "amount": "100",
                            "tenders": [
                                {"tender_type": "Card", "amount": "80"},
                                {"tender_type": "Cash", "amount": "20"},
                            ],
                        }
                    ],
                )
                await service.ingest(
                    db,
                    "Processor",
                    "two",
                    [
                        {
                            **common,
                            "source_record_id": "processor",
                            "record_type": "Capture",
                            "amount": "80",
                        }
                    ],
                )
                rows = (await db.execute(select(CanonicalTransaction))).scalars().all()
                pos = next(row for row in rows if row.transaction_type == "Sale")
                for row in rows:
                    row.reconciliation_status = "Exception"
                db.add(
                    TransactionAdjustment(
                        id="correction",
                        transaction_id=pos.id,
                        adjustment_type="Amount",
                        proposed_values={"amount_delta": "-10"},
                        status="Approved",
                        rationale="verified",
                        proposed_by="analyst",
                    )
                )
                await db.commit()
                await requeue_linked_exceptions(db, pos, rows, "finance")
                assert {row.reconciliation_status for row in rows} == {"Exception"}
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_unresolved_correction_keeps_original_case_without_creating_a_duplicate():
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
                    "one",
                    [{**common, "source_record_id": "pos", "record_type": "Sale", "amount": "100"}],
                )
                await service.ingest(
                    db,
                    "Processor",
                    "two",
                    [
                        {
                            **common,
                            "source_record_id": "processor",
                            "record_type": "Capture",
                            "amount": "90",
                        }
                    ],
                )
                await reconciliation.reconcile(db, "2026-10-01")
                await db.commit()
                rows = (await db.execute(select(CanonicalTransaction))).scalars().all()
                processor = next(row for row in rows if row.transaction_type == "Capture")
                db.add(
                    TransactionAdjustment(
                        id="correction",
                        transaction_id=processor.id,
                        adjustment_type="Amount",
                        proposed_values={"amount_delta": "5"},
                        status="Proposed",
                        rationale="verified",
                        proposed_by="analyst",
                    )
                )
                await db.commit()
                await approve_adjustment("correction", db, {"user": "finance"})
                assert {row.reconciliation_status for row in rows} == {"Exception"}
                result = await reconciliation.reconcile(db, "2026-10-01")
                assert result["cases_created"] == 0
                assert len((await db.execute(select(Case))).scalars().all()) == 1
        finally:
            await engine.dispose()

    asyncio.run(run())
