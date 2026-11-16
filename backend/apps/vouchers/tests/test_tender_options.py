"""Tender aging must govern non-expiring vouchers and every escheat execution."""

import asyncio
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException
from models import Base
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.audit.models import AuditEvent
from apps.vouchers.models import Voucher
from apps.vouchers.routes import (
    EscheatBody,
    IssueBody,
    VoucherOptionsBody,
    escheat_report,
    escheat_vouchers,
    issue_voucher,
    list_vouchers,
    save_voucher_options,
    today,
)

FINANCE = {"user": "finance", "role": "finance"}
IT = {"user": "configurer", "role": "it"}


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


async def configure(db, days):
    return await save_voucher_options(
        VoucherOptionsBody(options=[{"tender_type": "GiftCard", "escheat_days": days}]),
        db,
        IT,
    )


def test_tender_policy_uses_issue_age_and_rechecks_policy_before_escheatment():
    async def scenario(db):
        now = date.fromisoformat(today())
        for number, days, tender, expiry in (
            ("BOUNDARY", 30, "giftcard", None),
            ("TOO-RECENT", 29, "GiftCard", None),
            ("EXPIRED-EARLY", 20, "GiftCard", (now - timedelta(days=1)).isoformat()),
            ("FALLBACK", 40, "Voucher", (now - timedelta(days=1)).isoformat()),
            ("EXPIRES-TODAY", 40, "Voucher", now.isoformat()),
        ):
            await issue_voucher(
                IssueBody(
                    voucher_number=number,
                    store_id="S1",
                    amount="25",
                    issued_on=(now - timedelta(days=days)).isoformat(),
                    tender_type=tender,
                    expires_on=expiry,
                ),
                db,
                FINANCE,
            )
        settings = await configure(db, 30)
        assert settings[0]["updated_by"] == "configurer"
        eligible = await list_vouchers("Eligible", None, None, 100, db, FINANCE)
        assert {row["voucher_number"] for row in eligible} == {"BOUNDARY", "FALLBACK"}
        boundary = next(row for row in eligible if row["voucher_number"] == "BOUNDARY")
        assert boundary["eligible_on"] == now.isoformat()
        assert boundary["eligibility_basis"] == "Tender policy"
        assert boundary["status"] == "Issued"

        # A changed policy invalidates a previous preview; the entire request is rejected.
        await configure(db, 31)
        with pytest.raises(HTTPException) as changed:
            await escheat_vouchers(
                EscheatBody(voucher_numbers=["BOUNDARY", "FALLBACK"]), db, FINANCE
            )
        assert changed.value.status_code == 422
        assert (
            await db.scalar(select(Voucher).where(Voucher.voucher_number == "FALLBACK"))
        ).status == "Issued"
        await configure(db, 30)
        await escheat_vouchers(EscheatBody(voucher_numbers=["BOUNDARY"]), db, FINANCE)
        event = await db.scalar(
            select(AuditEvent).where(AuditEvent.event_type == "VOUCHERS_ESCHEATED")
        )
        assert event.after_state["eligibility"] == [
            {
                "voucher_number": "BOUNDARY",
                "tender_type": "giftcard",
                "eligible_on": now.isoformat(),
                "escheat_days": 30,
            }
        ]

    run_with_db(scenario)


def test_report_filters_eligibility_before_limit_and_preserves_store_scope():
    async def scenario(db):
        await configure(db, 36500)
        now = date.fromisoformat(today())
        db.add_all(
            [
                Voucher(
                    id=str(uuid.uuid4()),
                    voucher_number=f"NOT-DUE-{i}",
                    issued_store_id="S1",
                    issued_on=(now - timedelta(days=100)).isoformat(),
                    tender_type="GiftCard",
                    amount=Decimal("5"),
                    currency="USD",
                    issued_by="seed",
                )
                for i in range(510)
            ]
        )
        db.add(
            Voucher(
                id=str(uuid.uuid4()),
                voucher_number="DUE",
                issued_store_id="S2",
                issued_on=(now - timedelta(days=10)).isoformat(),
                tender_type="Voucher",
                expires_on=(now - timedelta(days=1)).isoformat(),
                amount=Decimal("5"),
                currency="USD",
                issued_by="seed",
            )
        )
        await db.commit()
        report = await escheat_report(0, db, FINANCE)
        assert [row["voucher_number"] for row in report["vouchers"]] == ["DUE"]
        assert report["truncated"] is False
        scoped = {**FINANCE, "store_scoped": True, "stores": ["S1"]}
        assert (await escheat_report(0, db, scoped))["vouchers"] == []

    run_with_db(scenario)


def test_duplicate_tenders_are_rejected_and_mixed_currencies_are_never_summed():
    async def scenario(db):
        with pytest.raises(HTTPException) as duplicate:
            await save_voucher_options(
                VoucherOptionsBody(
                    options=[
                        {"tender_type": "GiftCard", "escheat_days": 30},
                        {"tender_type": " giftcard ", "escheat_days": 60},
                    ]
                ),
                db,
                IT,
            )
        assert duplicate.value.status_code == 422
        with pytest.raises(ValidationError):
            VoucherOptionsBody(options=[{"tender_type": "GiftCard", "escheat_days": -1}])
        await configure(db, 0)
        for currency in ("USD", "EUR"):
            await issue_voucher(
                IssueBody(
                    voucher_number=currency,
                    store_id="S1",
                    amount="25",
                    currency=currency,
                    tender_type="GiftCard",
                ),
                db,
                FINANCE,
            )
        result = await escheat_vouchers(EscheatBody(voucher_numbers=["USD", "EUR"]), db, FINANCE)
        assert result == {
            "escheated": 2,
            "amount": None,
            "amounts": {"USD": Decimal("25"), "EUR": Decimal("25")},
        }

    run_with_db(scenario)


def test_migration_preserves_existing_vouchers_and_can_be_reversed():
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect, text

    path = Path(__file__).resolve().parents[3] / "alembic/versions/0110_voucher_tender_options.py"
    spec = importlib.util.spec_from_file_location("voucher_options_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE vouchers (id VARCHAR(36) PRIMARY KEY)"))
            connection.execute(text("INSERT INTO vouchers (id) VALUES ('existing')"))
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                assert (
                    connection.execute(text("SELECT tender_type FROM vouchers")).scalar_one()
                    == "Voucher"
                )
                migration.downgrade()
                assert (
                    connection.execute(text("SELECT id FROM vouchers")).scalar_one() == "existing"
                )
                assert "voucher_options" not in inspect(connection).get_table_names()
                assert {
                    column["name"] for column in inspect(connection).get_columns("vouchers")
                } == {"id"}
    finally:
        engine.dispose()
