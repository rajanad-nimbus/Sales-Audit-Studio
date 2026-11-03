"""Vouchers are redeemed once, expire by date, and only expired unclaimed ones can be escheated."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException
from models import Base
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.vouchers.routes import (
    EscheatBody,
    IssueBody,
    RedeemBody,
    escheat_report,
    escheat_vouchers,
    issue_voucher,
    list_vouchers,
    redeem_voucher,
    voucher_summary,
)

FINANCE = {"user": "finance", "role": "finance"}
TODAY = datetime.now(UTC).date()
PAST = (TODAY - timedelta(days=40)).isoformat()
LATER = (TODAY + timedelta(days=30)).isoformat()


def run_with_db(scenario):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                await scenario(db)
        finally:
            await engine.dispose()

    asyncio.run(run())


async def issue(db, number, *, expires=None, issued=PAST, store="S1", amount="25"):
    return await issue_voucher(
        IssueBody(
            voucher_number=number,
            store_id=store,
            amount=amount,
            issued_on=issued,
            expires_on=expires,
        ),
        db,
        FINANCE,
    )


def test_a_voucher_redeems_once_and_not_after_it_expires():
    async def scenario(db):
        await issue(db, "V1", expires=LATER)
        await issue(db, "V2", expires=PAST)
        redeemed = await redeem_voucher("V1", RedeemBody(store_id="S2"), db, FINANCE)
        assert redeemed["status"] == "Redeemed" and redeemed["redeemed_store_id"] == "S2"
        with pytest.raises(HTTPException) as twice:
            await redeem_voucher("V1", RedeemBody(store_id="S1"), db, FINANCE)
        assert twice.value.status_code == 409 and "Already redeemed at S2" in twice.value.detail
        with pytest.raises(HTTPException) as expired:
            await redeem_voucher("V2", RedeemBody(store_id="S1"), db, FINANCE)
        assert "expired" in expired.value.detail
        with pytest.raises(HTTPException) as duplicate:
            await issue(db, "V1")
        assert duplicate.value.status_code == 409

    run_with_db(scenario)


def test_issue_rules_and_status_filters():
    async def scenario(db):
        with pytest.raises(ValidationError):
            IssueBody(voucher_number="X", store_id="S1", amount="0")
        with pytest.raises(HTTPException) as backwards:
            await issue(db, "B", issued=LATER, expires=PAST)
        assert backwards.value.status_code == 422
        await issue(db, "OPEN", expires=LATER)
        await issue(db, "OLD", expires=PAST)
        await issue(db, "FOREVER")
        names = lambda rows: {r["voucher_number"] for r in rows}  # noqa: E731
        assert names(await list_vouchers("Expired", None, None, 100, db, FINANCE)) == {"OLD"}
        assert names(await list_vouchers("Issued", None, None, 100, db, FINANCE)) == {
            "OPEN",
            "FOREVER",
        }
        summary = await voucher_summary(db, FINANCE)
        assert summary["outstanding"]["USD"] == {"vouchers": 2, "amount": Decimal("50")}
        assert summary["expired"]["USD"] == {"vouchers": 1, "amount": Decimal("25")}

    run_with_db(scenario)


def test_only_expired_unredeemed_vouchers_can_be_escheated():
    async def scenario(db):
        await issue(db, "OLD1", expires=PAST, amount="10")
        await issue(db, "OLD2", expires=PAST, store="S2", amount="5")
        await issue(db, "LIVE", expires=LATER)
        report = await escheat_report(0, db, FINANCE)
        assert [(r["store_id"], r["vouchers"], r["amount"]) for r in report["by_store"]] == [
            ("S1", 1, Decimal("10")),
            ("S2", 1, Decimal("5")),
        ]
        # A longer waiting period leaves nothing that is old enough yet.
        assert (await escheat_report(365, db, FINANCE))["vouchers"] == []

        with pytest.raises(HTTPException) as live:
            await escheat_vouchers(EscheatBody(voucher_numbers=["OLD1", "LIVE"]), db, FINANCE)
        assert live.value.status_code == 422 and "LIVE" in live.value.detail
        done = await escheat_vouchers(EscheatBody(voucher_numbers=["OLD1", "OLD2"]), db, FINANCE)
        assert done == {"escheated": 2, "amount": Decimal("15")}
        with pytest.raises(HTTPException) as gone:
            await redeem_voucher("OLD1", RedeemBody(store_id="S1"), db, FINANCE)
        assert "escheated" in gone.value.detail
        manager = {"user": "m", "role": "finance", "store_scoped": True, "stores": ["S1"]}
        with pytest.raises(HTTPException) as scoped:
            await escheat_vouchers(EscheatBody(voucher_numbers=["LIVE"]), db, manager)
        assert scoped.value.status_code == 403

    run_with_db(scenario)
