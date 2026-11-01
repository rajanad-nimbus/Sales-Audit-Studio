"""Cash expectations respect the cashier and register dimensions of a declaration."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from apps.cash.routes import (
    CashControlBody,
    CashDepositBody,
    declare_cash,
    expected_cash_deposit,
    record_deposit,
)
from fastapi import HTTPException
from models import AuditEvent, Base, CanonicalTransaction, CashControl, CashDeposit, StoreDay
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


def test_cashier_declarations_use_cashier_scoped_sales_and_blank_cashier_stays_register_wide():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with sessions() as db:
                db.add(
                    StoreDay(
                        id="day-1",
                        store_id="S1",
                        business_date="2026-10-01",
                        status="Auditing",
                    )
                )
                event_time = datetime(2026, 10, 1, tzinfo=UTC)
                for transaction_id, cashier_id, amount in (
                    ("tx-1", "C1", "10.00"),
                    ("tx-2", "C2", "20.00"),
                ):
                    db.add(
                        CanonicalTransaction(
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
                        )
                    )
                await db.flush()

                finance = {"user": "finance@example.test", "role": "finance"}
                cashier_one = await declare_cash(
                    CashControlBody(
                        store_id="S1",
                        business_date="2026-10-01",
                        register_id="R1",
                        cashier_id="C1",
                        declared_cash=Decimal("10.00"),
                    ),
                    db,
                    finance,
                )
                cashier_two = await declare_cash(
                    CashControlBody(
                        store_id="S1",
                        business_date="2026-10-01",
                        register_id="R1",
                        cashier_id="C2",
                        declared_cash=Decimal("20.00"),
                    ),
                    db,
                    finance,
                )
                cash_scope_queries = []

                @event.listens_for(db.get_bind(), "before_cursor_execute")
                def capture_cash_scope_query(
                    _connection, _cursor, statement, _parameters, _context, _executemany
                ):
                    if "from cash_controls" in statement.lower():
                        cash_scope_queries.append(statement)

                query_count = len(cash_scope_queries)
                assert await expected_cash_deposit(db, "S1", "2026-10-01") == Decimal("30.00")
                assert len(cash_scope_queries) - query_count == 1
                register = await declare_cash(
                    CashControlBody(
                        store_id="S1",
                        business_date="2026-10-01",
                        register_id="R1",
                        declared_cash=Decimal("30.00"),
                    ),
                    db,
                    finance,
                )
                query_count = len(cash_scope_queries)
                deposit = await record_deposit(
                    CashDepositBody(
                        store_id="S1",
                        business_date="2026-10-01",
                        deposit_reference="BANK-REF-1",
                        deposited_amount=Decimal("30.00"),
                    ),
                    db,
                    finance,
                )
                assert len(cash_scope_queries) - query_count == 1
                other_register_cashier = await declare_cash(
                    CashControlBody(
                        store_id="S1",
                        business_date="2026-10-01",
                        register_id="R2",
                        cashier_id="C3",
                        declared_cash=Decimal("5.00"),
                    ),
                    db,
                    finance,
                )
                deposit_row = await db.get(CashDeposit, deposit["id"])
                assert deposit_row.expected_amount == Decimal("35.00")
                assert deposit_row.variance_amount == Decimal("5.00")
                assert deposit_row.status == "Over/Short"
                query_count = len(cash_scope_queries)
                assert await expected_cash_deposit(db, "S1", "2026-10-01") == Decimal("35.00")
                assert len(cash_scope_queries) - query_count == 1
                store_wide = await declare_cash(
                    CashControlBody(
                        store_id="S1",
                        business_date="2026-10-01",
                        declared_cash=Decimal("40.00"),
                    ),
                    db,
                    finance,
                )
                assert deposit_row.expected_amount == Decimal("40.00")
                assert deposit_row.variance_amount == Decimal("10.00")
                query_count = len(cash_scope_queries)
                assert await expected_cash_deposit(db, "S1", "2026-10-01") == Decimal("40.00")
                assert len(cash_scope_queries) - query_count == 1

                assert cashier_one["expected_amount"] == Decimal("10.00")
                assert cashier_two["expected_amount"] == Decimal("20.00")
                assert register["expected_amount"] == Decimal("30.00")
                assert all(
                    item["status"] == "Balanced" for item in (cashier_one, cashier_two, register)
                )
                assert deposit["expected_amount"] == Decimal("30.00")
                assert deposit["variance_amount"] == Decimal("0.00")
                assert deposit["status"] == "Balanced"
                events = (
                    (
                        await db.execute(
                            select(AuditEvent).where(
                                AuditEvent.object_id.in_(
                                    [
                                        cashier_one["id"],
                                        cashier_two["id"],
                                        register["id"],
                                        other_register_cashier["id"],
                                        store_wide["id"],
                                        deposit["id"],
                                    ]
                                )
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                assert {event.event_type for event in events} == {
                    "CASH_CONTROL_DECLARED",
                    "CASH_DEPOSIT_RECORDED",
                    "CASH_DEPOSIT_RECONCILED",
                }
                deposit_event = next(
                    event
                    for event in events
                    if event.object_id == deposit["id"]
                    and event.event_type == "CASH_DEPOSIT_RECORDED"
                )
                assert deposit_event.actor == finance["user"]
                assert deposit_event.after_state["deposited_amount"] == "30.00"
                assert deposit_event.after_state["status"] == "Balanced"
                reconciliation_events = [
                    event
                    for event in events
                    if event.object_id == deposit["id"]
                    and event.event_type == "CASH_DEPOSIT_RECONCILED"
                ]
                assert len(reconciliation_events) == 2
                assert reconciliation_events[-1].after_state["expected_amount"] == "40.00"
                assert reconciliation_events[-1].after_state["status"] == "Over/Short"
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
                db.add(
                    StoreDay(
                        id="day-1",
                        store_id="S1",
                        business_date="2026-10-01",
                        status="Auditing",
                    )
                )
                await db.flush()
                finance = {"user": "finance@example.test", "role": "finance"}
                deposit = CashDepositBody(
                    store_id="S1",
                    business_date="2026-10-01",
                    deposit_reference="BANK-REF-1",
                    deposited_amount=Decimal("10.00"),
                )
                await record_deposit(deposit, db, finance)

                with pytest.raises(HTTPException) as error:
                    await record_deposit(deposit, db, finance)

                assert error.value.status_code == 409
                assert error.value.detail == "Deposit reference already exists"
                audit_rows = (
                    (
                        await db.execute(
                            select(AuditEvent).where(
                                AuditEvent.event_type == "CASH_DEPOSIT_RECORDED"
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                assert len(audit_rows) == 1
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_cash_reconciliation_rejects_mixed_store_day_currencies():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with sessions() as db:
                event_time = datetime(2026, 10, 1, tzinfo=UTC)
                db.add(
                    StoreDay(
                        id="day-1",
                        store_id="S1",
                        business_date="2026-10-01",
                        status="Auditing",
                    )
                )
                for transaction_id, currency in (("tx-usd", "USD"), ("tx-eur", "EUR")):
                    db.add(
                        CanonicalTransaction(
                            id=transaction_id,
                            business_date="2026-10-01",
                            transaction_date=event_time,
                            event_timestamp=event_time,
                            processing_timestamp=event_time,
                            settlement_date="2026-10-01",
                            transaction_type="Sale",
                            source_lineage="pos-map-v1",
                            currency=currency,
                            signed_amount=Decimal("10.00"),
                            store_id="S1",
                            register_id="R1",
                            channel_id="Store",
                            tender_type="Cash",
                        )
                    )
                await db.commit()
                finance = {"user": "finance@example.test", "role": "finance"}

                with pytest.raises(HTTPException, match="multiple currencies") as declaration_error:
                    await declare_cash(
                        CashControlBody(
                            store_id="S1",
                            business_date="2026-10-01",
                            register_id="R1",
                            declared_cash=Decimal("20.00"),
                        ),
                        db,
                        finance,
                    )
                assert declaration_error.value.status_code == 409
                await db.rollback()

                with pytest.raises(HTTPException, match="multiple currencies") as deposit_error:
                    await record_deposit(
                        CashDepositBody(
                            store_id="S1",
                            business_date="2026-10-01",
                            deposit_reference="BANK-REF",
                            deposited_amount=Decimal("20.00"),
                        ),
                        db,
                        finance,
                    )
                assert deposit_error.value.status_code == 409
                await db.rollback()
                assert (await db.execute(select(CashControl))).scalars().all() == []
                assert (await db.execute(select(CashDeposit))).scalars().all() == []
        finally:
            await engine.dispose()

    asyncio.run(run())
