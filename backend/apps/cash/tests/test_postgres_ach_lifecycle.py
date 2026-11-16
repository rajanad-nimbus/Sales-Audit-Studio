"""ACH adjustments on PostgreSQL: add, summarize by settlement destination, reverse once, audit each step."""

import asyncio
import os
import uuid
from decimal import Decimal

import pytest
from apps.cash.ach_routes import (
    ACHAdjustmentBody,
    add_adjustment,
    reverse_adjustment,
    settlement_summary,
)
from fastapi import HTTPException
from models import AuditEvent, Base
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

POSTGRES_URL = os.getenv("ZETSA_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="ZETSA_TEST_POSTGRES_URL not configured")

FINANCE = {"user": "finance", "role": "finance"}
MANAGER = {"user": "m", "role": "finance", "store_scoped": True, "stores": ["S1"]}
DAY = "2026-10-01"


def body(record_type, amount, *, store=None, bank="BANK-1", note="Deposit shortfall"):
    return ACHAdjustmentBody(
        record_type=record_type,
        store_id=store,
        bank_id=bank,
        business_date=DAY,
        adjustment_amount=Decimal(amount),
        note=note,
    )


async def as_request(sessions, call):
    """Run one call the way a request does: its own session, closed when it ends."""
    async with sessions() as db:
        return await call(db)


def with_postgres(scenario):
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
            await scenario(async_sessionmaker(engine, expire_on_commit=False))
        finally:
            if engine:
                await engine.dispose()
            async with admin.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await admin.dispose()

    asyncio.run(run())


def test_adjustments_settle_by_destination_and_a_reversal_removes_one_from_the_summary():
    async def scenario(sessions):
        async with sessions() as db:
            first = await add_adjustment(body("Store", "-12.50", store="S1"), db, FINANCE)
            await add_adjustment(body("Store", "30.00", store="S1"), db, FINANCE)
            await add_adjustment(body("Bank", "100.00"), db, FINANCE)

            summary = await settlement_summary("Store", DAY, "S1", None, db, FINANCE)
            assert summary["settlement_date"] == "2026-10-02"  # the next day
            [item] = summary["items"]
            assert item["store_id"] == "S1" and item["active_adjustments"] == 2
            assert item["net_adjustment"] == Decimal("17.50")

            with pytest.raises(HTTPException) as hq_only:
                await add_adjustment(body("Bank", "5.00"), db, MANAGER)
            assert hq_only.value.status_code == 403
            with pytest.raises(HTTPException) as elsewhere:
                await add_adjustment(body("Store", "5.00", store="S2"), db, MANAGER)
            assert elsewhere.value.status_code == 403

            reversed_row = await reverse_adjustment(first["id"], "Entered in error", db, FINANCE)
            assert reversed_row["status"] == "Reversed"
            after = await settlement_summary("Store", DAY, "S1", None, db, FINANCE)
            [item] = after["items"]
            assert item["active_adjustments"] == 1 and item["net_adjustment"] == Decimal("30.00")
            with pytest.raises(HTTPException) as again:
                await reverse_adjustment(first["id"], "Entered in error", db, FINANCE)
            assert again.value.status_code == 409

            events = [
                e.event_type
                for e in (await db.execute(select(AuditEvent).order_by(AuditEvent.created_at)))
                .scalars()
                .all()
            ]
            assert events.count("ACH_ADJUSTMENT_ADDED") == 3
            assert events.count("ACH_ADJUSTMENT_REVERSED") == 1

    with_postgres(scenario)


def test_two_finance_users_reversing_the_same_adjustment_at_once_get_one_reversal():
    async def scenario(sessions):
        async with sessions() as db:
            adjustment = await add_adjustment(body("Store", "40.00", store="S1"), db, FINANCE)
        outcomes = await asyncio.gather(
            as_request(
                sessions, lambda db: reverse_adjustment(adjustment["id"], "First", db, FINANCE)
            ),
            as_request(
                sessions, lambda db: reverse_adjustment(adjustment["id"], "Second", db, FINANCE)
            ),
            return_exceptions=True,
        )
        assert sum(isinstance(o, dict) for o in outcomes) == 1
        losses = [o for o in outcomes if isinstance(o, HTTPException)]
        assert len(losses) == 1 and losses[0].status_code == 409
        async with sessions() as db:
            reversals = (
                (
                    await db.execute(
                        select(AuditEvent).where(AuditEvent.event_type == "ACH_ADJUSTMENT_REVERSED")
                    )
                )
                .scalars()
                .all()
            )
            assert len(reversals) == 1

    with_postgres(scenario)
