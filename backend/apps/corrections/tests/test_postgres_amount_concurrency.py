"""Run against a disposable database using ZETSA_TEST_POSTGRES_URL."""

import asyncio
import os
import uuid
from decimal import Decimal

import pytest
from fastapi import HTTPException
from models import Base, CanonicalTransaction, Case, Recommendation, TransactionAdjustment
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.corrections.effects import effective_amounts
from apps.corrections.routes import approve_adjustment
from apps.ingest import reconciliation
from apps.ingest.service import ingest

POSTGRES_URL = os.getenv("ZETSA_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="ZETSA_TEST_POSTGRES_URL not configured")


def test_concurrent_approvals_cannot_overflow_without_a_store_day():
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
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with sessions() as db:
                await ingest(
                    db,
                    "POS",
                    "delivery",
                    [
                        {
                            "source_record_id": "sale",
                            "record_type": "Sale",
                            "event_time": "2026-10-01T10:00:00Z",
                            "store_id": "S1",
                            "amount": "9999999999998.99",
                        }
                    ],
                )
                transaction = (await db.execute(select(CanonicalTransaction))).scalar_one()
                db.add_all(
                    [
                        TransactionAdjustment(
                            id=identifier,
                            transaction_id=transaction.id,
                            adjustment_type="Amount",
                            proposed_values={"amount_delta": "1"},
                            status="Proposed",
                            rationale="test",
                            proposed_by="analyst",
                        )
                        for identifier in ("a", "b")
                    ]
                )
                await db.commit()
            async with sessions() as first, sessions() as second:
                results = await asyncio.gather(
                    approve_adjustment("a", first, {"user": "one"}),
                    approve_adjustment("b", second, {"user": "two"}),
                    return_exceptions=True,
                )
                assert sum(isinstance(result, dict) for result in results) == 1
                assert (
                    sum(
                        isinstance(result, HTTPException) and result.status_code == 422
                        for result in results
                    )
                    == 1
                )
            async with sessions() as db:
                transaction = (await db.execute(select(CanonicalTransaction))).scalar_one()
                assert (await effective_amounts(db, [transaction]))[transaction.id] == Decimal(
                    "9999999999999.99"
                )
                statuses = (await db.execute(select(TransactionAdjustment.status))).scalars().all()
                assert sorted(statuses) == ["Approved", "Proposed"]
                common = {
                    "event_time": "2026-10-02T10:00:00Z",
                    "store_id": "S1",
                    "payment_reference": "pair",
                }
                await ingest(
                    db,
                    "POS",
                    "pos",
                    [{**common, "source_record_id": "pos", "record_type": "Sale", "amount": "100"}],
                )
                await ingest(
                    db,
                    "Processor",
                    "processor",
                    [
                        {
                            **common,
                            "source_record_id": "capture",
                            "record_type": "Capture",
                            "amount": "90",
                        }
                    ],
                )
                await reconciliation.reconcile(db, "2026-10-02")
                rows = (
                    (
                        await db.execute(
                            select(CanonicalTransaction).where(
                                CanonicalTransaction.payment_reference == "pair"
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                processor = next(row for row in rows if row.transaction_type == "Capture")
                case = (
                    await db.execute(select(Case).where(Case.business_date == "2026-10-02"))
                ).scalar_one()
                case.status, case.investigation_status, case.evidence_completeness = (
                    "In Review",
                    "Ready for Decision",
                    100,
                )
                db.add(
                    Recommendation(
                        id="pair-rec",
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
                        id="pair-correction",
                        transaction_id=processor.id,
                        adjustment_type="Amount",
                        proposed_values={"amount_delta": "10"},
                        status="Proposed",
                        rationale="test",
                        proposed_by="analyst",
                    )
                )
                await db.commit()
                await approve_adjustment("pair-correction", db, {"user": "finance"})
                assert {row.reconciliation_status for row in rows} == {"Unmatched"}
                assert (case.status, case.evidence_completeness) == ("Open", 0)
                assert (await db.get(Recommendation, "pair-rec")).status == "Superseded"
                assert (await reconciliation.reconcile(db, "2026-10-02"))["matched_pairs"] == 1
        finally:
            if engine:
                await engine.dispose()
            async with admin.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await admin.dispose()

    asyncio.run(run())
