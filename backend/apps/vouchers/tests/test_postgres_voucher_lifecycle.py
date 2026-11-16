"""Voucher issue, redemption and escheatment on PostgreSQL, including the races row locks must settle."""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException
from models import Base, Voucher
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.vouchers.routes import (
    EscheatBody,
    IssueBody,
    RedeemBody,
    escheat_report,
    escheat_vouchers,
    issue_voucher,
    redeem_voucher,
    voucher_summary,
)

POSTGRES_URL = os.getenv("ZETSA_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="ZETSA_TEST_POSTGRES_URL not configured")

FINANCE = {"user": "finance", "role": "finance"}
TODAY = datetime.now(UTC).date()
PAST = (TODAY - timedelta(days=40)).isoformat()
LATER = (TODAY + timedelta(days=30)).isoformat()


async def issue(db, number, *, expires=None, store="S1", amount="25"):
    return await issue_voucher(
        IssueBody(
            voucher_number=number,
            store_id=store,
            amount=amount,
            issued_on=PAST,
            expires_on=expires,
        ),
        db,
        FINANCE,
    )


async def as_request(sessions, call):
    """Run one call the way a request does: its own session, closed (and rolled back) when it ends."""
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


def test_a_voucher_is_issued_redeemed_once_and_escheated_when_unclaimed():
    async def scenario(sessions):
        async with sessions() as db:
            await issue(db, "LIVE", expires=LATER, amount="40")
            await issue(db, "UNCLAIMED", expires=PAST, amount="15")
            await issue(db, "SPENT", expires=LATER, amount="10")
            await redeem_voucher("SPENT", RedeemBody(store_id="S2"), db, FINANCE)

            summary = await voucher_summary(db, FINANCE)
            assert summary["outstanding"]["USD"]["amount"] == Decimal("40")
            assert summary["expired"]["USD"]["amount"] == Decimal("15")

            report = await escheat_report(0, db, FINANCE)
            assert [v["voucher_number"] for v in report["vouchers"]] == ["UNCLAIMED"]
            done = await escheat_vouchers(EscheatBody(voucher_numbers=["UNCLAIMED"]), db, FINANCE)
            assert done["escheated"] == 1 and done["amount"] == Decimal("15")
            statuses = {
                v.voucher_number: v.status
                for v in (await db.execute(select(Voucher))).scalars().all()
            }
            assert statuses == {"LIVE": "Issued", "UNCLAIMED": "Escheated", "SPENT": "Redeemed"}

    with_postgres(scenario)


def test_two_stores_redeeming_the_same_voucher_at_once_get_exactly_one_success():
    async def scenario(sessions):
        async with sessions() as db:
            await issue(db, "ONCE", expires=LATER)
        outcomes = await asyncio.gather(
            as_request(
                sessions, lambda db: redeem_voucher("ONCE", RedeemBody(store_id="S1"), db, FINANCE)
            ),
            as_request(
                sessions, lambda db: redeem_voucher("ONCE", RedeemBody(store_id="S2"), db, FINANCE)
            ),
            return_exceptions=True,
        )
        wins = [o for o in outcomes if isinstance(o, dict)]
        losses = [o for o in outcomes if isinstance(o, HTTPException)]
        assert len(wins) == 1 and len(losses) == 1 and losses[0].status_code == 409
        assert "Already redeemed" in losses[0].detail
        async with sessions() as db:
            voucher = (await db.execute(select(Voucher))).scalar_one()
            assert voucher.status == "Redeemed"
            assert voucher.redeemed_store_id == wins[0]["redeemed_store_id"]

    with_postgres(scenario)


def test_a_redemption_and_an_escheatment_of_the_same_voucher_cannot_both_win():
    async def scenario(sessions):
        async with sessions() as db:
            await issue(db, "RACE", expires=PAST)
        outcomes = await asyncio.gather(
            as_request(
                sessions, lambda db: redeem_voucher("RACE", RedeemBody(store_id="S1"), db, FINANCE)
            ),
            as_request(
                sessions,
                lambda db: escheat_vouchers(EscheatBody(voucher_numbers=["RACE"]), db, FINANCE),
            ),
            return_exceptions=True,
        )
        # An expired voucher cannot be redeemed, so escheatment wins; it must never be both.
        assert sum(isinstance(o, dict) for o in outcomes) == 1
        async with sessions() as db:
            voucher = (await db.execute(select(Voucher))).scalar_one()
            assert voucher.status == "Escheated" and voucher.redeemed_store_id is None

    with_postgres(scenario)
