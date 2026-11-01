"""Governed correction controls."""
import asyncio

import pytest
from fastapi import HTTPException
from models import (
    AuditEvent,
    AuditTotal,
    Base,
    CanonicalTransaction,
    SourceRecord,
    StoreDay,
    TransactionAdjustment,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.sql.dml import Update

from apps.corrections.routes import (
    approve_adjustment,
    reject_adjustment,
    validate_adjustment_values,
    validate_missing_transaction,
)
from apps.ingest.service import ingest
from apps.store_days.routes import retotal_store_day


def test_approved_missing_transaction_creates_immutable_lineage():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(engine, expire_on_commit=False)
        async with Session() as db:
            db.add(TransactionAdjustment(
                id="adjustment-1", transaction_id=None, adjustment_type="MissingTransaction",
                proposed_values={"business_date": "2026-10-06", "store_id": "S1", "amount": "12.50", "transaction_type": "Sale", "tender_type": "Cash"},
                rationale="Verified receipt", status="Proposed", proposed_by="analyst", reviewed_by=None, reviewed_at=None,
            ))
            await db.commit()
            result = await approve_adjustment("adjustment-1", db, {"user": "finance.user"})
            transaction = (await db.execute(select(CanonicalTransaction))).scalar_one()
            source = (await db.execute(select(SourceRecord))).scalar_one()
            assert result["status"] == "Approved"
            assert result["transaction_id"] == transaction.id
            assert str(transaction.signed_amount) == "12.50"
            assert transaction.source_record_id == source.id
            assert source.source_system == "ManualAdjustment"
        await engine.dispose()
    asyncio.run(run())


@pytest.mark.parametrize("changes", [
    {"amount": "NaN"}, {"amount": "10000000000000"}, {"amount": "0.001"},
    {"business_date": "2026-02-30"}, {"business_date": "2026-10-01T00:00:00"},
    {"settlement_date": "invalid"}, {"event_timestamp": "invalid"}, {"store_id": " "},
    {"transaction_type": "Unknown"}, {"currency": "US"}, {"quantity": "abc"},
    {"register_id": "x" * 51},
    {"amount_delta": "10"},
])
def test_invalid_missing_transaction_is_rejected(changes):
    values = {"business_date": "2026-10-06", "store_id": "S1", "amount": "12.50", "transaction_type": "Sale"}
    with pytest.raises(HTTPException) as error:
        validate_missing_transaction({**values, **changes})
    assert error.value.status_code == 422


@pytest.mark.parametrize("kind,values", [
    ("Amount", {"amount_delta": "NaN"}), ("Amount", {"amount_delta": "Infinity"}),
    ("Amount", {"amount_delta": "10000000000000"}), ("Amount", {"amount_delta": "0.001"}),
    ("Amount", {"amount_delta": "1", "corrected_amount": "2"}),
    ("Tax", {"tax_delta": "1", "amount_delta": "100"}),
    ("Reference", {"reference": "ok", "corrected_amount": "100"}),
    ("Tender", {"tender_type": ""}), ("Item", {"item": []}),
])
def test_invalid_adjustment_values_are_rejected(kind, values):
    with pytest.raises(HTTPException) as error:
        validate_adjustment_values(kind, values)
    assert error.value.status_code == 422


@pytest.mark.parametrize("kind,values", [
    ("Amount", {"amount_delta": "-12.50"}), ("Amount", {"corrected_amount": "0"}),
    ("Tax", {"tax_delta": "-1.25"}), ("Tender", {"tender_type": "Cash"}),
    ("Reference", {"reference": "ref"}), ("Item", {"item": "SKU"}),
])
def test_valid_adjustment_values_are_accepted(kind, values):
    validate_adjustment_values(kind, values)


@pytest.mark.parametrize("kind,values", [
    ("MissingTransaction", {"business_date": "2026-10-06", "store_id": "S1", "amount": "NaN", "transaction_type": "Sale"}),
    ("Tax", {"tax_delta": "1", "amount_delta": "100"}),
])
def test_invalid_existing_proposal_is_not_approved(kind, values):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            session = async_sessionmaker(engine, expire_on_commit=False)
            async with session() as db:
                db.add(TransactionAdjustment(id="invalid", adjustment_type=kind, status="Proposed",
                                             proposed_values=values,
                                             rationale="test", proposed_by="analyst"))
                await db.commit()
                with pytest.raises(HTTPException) as error:
                    await approve_adjustment("invalid", db, {"user": "finance"})
                assert error.value.status_code == 422
                assert (await db.get(TransactionAdjustment, "invalid")).status == "Proposed"
                assert (await db.execute(select(CanonicalTransaction))).scalars().all() == []
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_legacy_non_amount_adjustment_cannot_change_store_day_net():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            session = async_sessionmaker(engine, expire_on_commit=False)
            async with session() as db:
                await ingest(db, "POS", "d1", [{"source_record_id": "s1", "event_time": "2026-10-06T10:00:00Z",
                                                   "record_type": "Sale", "amount": "10", "store_id": "S1"}])
                transaction = (await db.execute(select(CanonicalTransaction))).scalar_one()
                db.add(StoreDay(id="day", store_id="S1", business_date="2026-10-06"))
                db.add(TransactionAdjustment(id="legacy", transaction_id=transaction.id, adjustment_type="Tax",
                                             proposed_values={"tax_delta": "1", "amount_delta": "100"},
                                             status="Approved", rationale="test", proposed_by="analyst"))
                await db.commit()
                await retotal_store_day("S1", "2026-10-06", db, {"user": "finance", "role": "finance"})
                amount = (await db.execute(select(AuditTotal.calculated_amount).where(
                    AuditTotal.total_name == "POS Net", AuditTotal.level == "Store"))).scalar_one()
                assert amount == 10
        finally:
            await engine.dispose()

    asyncio.run(run())


@pytest.mark.parametrize("second_action", ["approve", "reject"])
def test_concurrent_reviews_apply_only_one_decision(tmp_path, monkeypatch, second_action):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            session = async_sessionmaker(engine, expire_on_commit=False)
            async with session() as db:
                db.add(TransactionAdjustment(id="a1", adjustment_type="MissingTransaction", status="Proposed",
                                             proposed_values={"business_date": "2026-10-06", "store_id": "S1",
                                                              "amount": "12.50", "transaction_type": "Sale"},
                                             rationale="verified", proposed_by="analyst"))
                await db.commit()

            ready = asyncio.Event()
            arrivals = 0

            async with session() as first, session() as second:
                for db in (first, second):
                    original = db.execute

                    async def synchronized_execute(statement, *args, _original=original, **kwargs):
                        nonlocal arrivals
                        if isinstance(statement, Update):
                            arrivals += 1
                            if arrivals == 2:
                                ready.set()
                            await asyncio.wait_for(ready.wait(), timeout=5)
                        return await _original(statement, *args, **kwargs)

                    monkeypatch.setattr(db, "execute", synchronized_execute)
                other = approve_adjustment if second_action == "approve" else reject_adjustment
                outcomes = await asyncio.gather(approve_adjustment("a1", first, {"user": "reviewer1"}),
                                                other("a1", second, {"user": "reviewer2"}), return_exceptions=True)
                assert sum(isinstance(outcome, dict) for outcome in outcomes) == 1
                errors = [outcome for outcome in outcomes if isinstance(outcome, Exception)]
                assert len(errors) == 1 and isinstance(errors[0], HTTPException) and errors[0].status_code == 409

            async with session() as db:
                adjustment = await db.get(TransactionAdjustment, "a1")
                transactions = (await db.execute(select(CanonicalTransaction))).scalars().all()
                events = (await db.execute(select(AuditEvent))).scalars().all()
                assert len(events) == 1
                assert len(transactions) == (1 if adjustment.status == "Approved" else 0)
        finally:
            await engine.dispose()

    asyncio.run(run())
