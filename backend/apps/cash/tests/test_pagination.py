"""Cash control list endpoints return bounded, filter-consistent pages."""

import asyncio

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import auth
from apps.cash import routes
from database import get_db
from models import Base, CashControl, CashDeposit


def test_cash_lists_paginate_and_report_consistent_totals():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with sessions() as db:
                for index in range(105):
                    db.add(CashControl(
                        id=f"control-{index:03}", store_id="S1", business_date="2026-10-01",
                        register_id=f"R{index}", tender_type="Cash", declared_by="finance",
                    ))
                    db.add(CashDeposit(
                        id=f"deposit-{index:03}", store_id="S1", business_date="2026-10-01",
                        deposit_reference=f"DEP-{index:03}", deposited_amount=0, recorded_by="finance",
                    ))
                await db.commit()

            app = FastAPI()
            app.include_router(routes.router)

            async def database_override():
                async with sessions() as db:
                    yield db

            async def user_override():
                return {"user": "finance", "role": "finance"}

            app.dependency_overrides[get_db] = database_override
            app.dependency_overrides[auth.current_user] = user_override
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                controls = await client.get("/api/cash-controls", params={"limit": 100})
                deposits = await client.get("/api/cash-controls/deposits", params={"limit": 100})
                assert len(controls.json()) == 100 and controls.headers["x-total-count"] == "105"
                assert controls.headers["x-has-more"] == "true"
                assert len(deposits.json()) == 100 and deposits.headers["x-total-count"] == "105"
                assert deposits.headers["x-has-more"] == "true"

                last_controls = await client.get("/api/cash-controls", params={"skip": 100, "limit": 100})
                last_deposits = await client.get("/api/cash-controls/deposits", params={"skip": 100, "limit": 100})
                assert len(last_controls.json()) == len(last_deposits.json()) == 5
                assert last_controls.headers["x-has-more"] == last_deposits.headers["x-has-more"] == "false"
                assert (await client.get("/api/cash-controls", params={"business_date": "2026-02-30"})).status_code == 422
        finally:
            await engine.dispose()
    asyncio.run(run())
