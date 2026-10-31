"""Cash expectations respect the cashier and register dimensions of a declaration."""
import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.cash.routes import (
    CashControlBody,
    CashDepositBody,
    declare_cash,
    expected_cash_deposit,
    record_deposit,
)
from models import Base, CanonicalTransaction, StoreDay


def test_cashier_declarations_use_cashier_scoped_sales_and_blank_cashier_stays_register_wide():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with sessions() as db:
                db.add(StoreDay(
                    id="day-1", store_id="S1", business_date="2026-10-01", status="Auditing",
                ))
                event_time = datetime(2026, 10, 1, tzinfo=timezone.utc)
                for transaction_id, cashier_id, amount in (
                    ("tx-1", "C1", "10.00"),
                    ("tx-2", "C2", "20.00"),
                ):
                    db.add(CanonicalTransaction(
                        id=transaction_id,
                        business_date="2026-10-01",
                        transaction_date=event_time,
                        event_timestamp=event_time,
                        processing_timestamp=event_time,
                        settlement_date="2026-10-01",
                        transaction_type="Sale",
                        source_lineage="pos-map-v1",
                        currency="USD",
                        signed_amount=Decimal(amount),
                        store_id="S1",
                        register_id="R1",
                        cashier_id=cashier_id,
                        channel_id="Store",
                        tender_type="Cash",
                    ))
                await db.flush()

                finance = {"user": "finance@example.test", "role": "finance"}
                cashier_one = await declare_cash(CashControlBody(
                    store_id="S1", business_date="2026-10-01", register_id="R1",
                    cashier_id="C1", declared_cash=Decimal("10.00"),
                ), db, finance)
                cashier_two = await declare_cash(CashControlBody(
                    store_id="S1", business_date="2026-10-01", register_id="R1",
                    cashier_id="C2", declared_cash=Decimal("20.00"),
                ), db, finance)
                assert await expected_cash_deposit(db, "S1", "2026-10-01") == Decimal("30.00")
                register = await declare_cash(CashControlBody(
                    store_id="S1", business_date="2026-10-01", register_id="R1",
                    declared_cash=Decimal("30.00"),
                ), db, finance)
                deposit = await record_deposit(CashDepositBody(
                    store_id="S1", business_date="2026-10-01",
                    deposit_reference="BANK-REF-1", deposited_amount=Decimal("30.00"),
                ), db, finance)

                assert cashier_one["expected_amount"] == Decimal("10.00")
                assert cashier_two["expected_amount"] == Decimal("20.00")
                assert register["expected_amount"] == Decimal("30.00")
                assert all(item["status"] == "Balanced" for item in (cashier_one, cashier_two, register))
                assert deposit["expected_amount"] == Decimal("30.00")
                assert deposit["variance_amount"] == Decimal("0.00")
                assert deposit["status"] == "Balanced"
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_duplicate_deposit_reference_is_conflict():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with sessions() as db:
                db.add(StoreDay(
                    id="day-1", store_id="S1", business_date="2026-10-01", status="Auditing",
                ))
                await db.flush()
                finance = {"user": "finance@example.test", "role": "finance"}
                deposit = CashDepositBody(
                    store_id="S1", business_date="2026-10-01",
                    deposit_reference="BANK-REF-1", deposited_amount=Decimal("10.00"),
                )
                await record_deposit(deposit, db, finance)

                with pytest.raises(HTTPException) as error:
                    await record_deposit(deposit, db, finance)

                assert error.value.status_code == 409
                assert error.value.detail == "Deposit reference already exists"
        finally:
            await engine.dispose()

    asyncio.run(run())
