"""Run every cash-office scenario: declarations and deposits against a store day's sales."""

import asyncio
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from apps.cash import scenarios
from apps.cash.routes import CashControlBody, CashDepositBody, declare_cash, record_deposit
from models import Base, CanonicalTransaction, CashControl, CashDeposit, StoreDay
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

ALL = scenarios.scenarios()
FINANCE = {"user": "finance@example.test", "role": "finance"}


def outcomes(scenario: scenarios.Scenario) -> list[tuple[str, str]]:
    async def go():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add(
                    StoreDay(
                        id="day",
                        store_id=scenarios.STORE,
                        business_date=scenarios.DAY,
                        status="Auditing",
                    )
                )
                stamp = datetime(2026, 10, 1, tzinfo=UTC)
                for register, cashier, amount, kind, tender in scenario.sales:
                    db.add(
                        CanonicalTransaction(
                            id=str(uuid.uuid4()),
                            business_date=scenarios.DAY,
                            transaction_date=stamp,
                            event_timestamp=stamp,
                            processing_timestamp=stamp,
                            settlement_date=scenarios.DAY,
                            transaction_type=kind,
                            source_lineage="pos-map-v1",
                            currency="USD",
                            signed_amount=Decimal(amount),
                            store_id=scenarios.STORE,
                            register_id=register,
                            cashier_id=cashier,
                            channel_id="Store",
                            tender_type=tender,
                        )
                    )
                await db.flush()
                made = []
                for n, (kind, payload) in enumerate(scenario.steps):
                    if kind == "declare":
                        body = CashControlBody(
                            store_id=scenarios.STORE, business_date=scenarios.DAY, **payload
                        )
                        view = await declare_cash(body, db, FINANCE)
                        made.append((CashControl, view["id"]))
                    else:
                        body = CashDepositBody(
                            store_id=scenarios.STORE,
                            business_date=scenarios.DAY,
                            deposit_reference=f"DEP-{n}",
                            deposited_amount=Decimal(payload),
                        )
                        view = await record_deposit(body, db, FINANCE)
                        made.append((CashDeposit, view["id"]))
                result = []
                for (
                    model,
                    row_id,
                ) in made:  # read after every step: later counts re-reconcile deposits
                    row = (await db.execute(select(model).where(model.id == row_id))).scalar_one()
                    await db.refresh(row)
                    result.append((row.status, f"{row.variance_amount:.2f}"))
                return result
        finally:
            await engine.dispose()

    return asyncio.run(go())


def test_scenario_names_are_unique():
    names = [s.name for s in ALL]
    assert len(names) == len(set(names))


@pytest.mark.parametrize("scenario", ALL, ids=[s.name for s in ALL])
def test_scenario_produces_the_expected_status_and_variance(scenario):
    assert outcomes(scenario) == scenario.expect


def test_every_scenario_states_one_result_per_step():
    assert all(len(s.steps) == len(s.expect) for s in ALL)
    assert {s.group for s in ALL} == {"register", "deposit"}
