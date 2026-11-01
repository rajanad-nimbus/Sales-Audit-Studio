"""Action Center filters reject malformed or reversed calendar-date ranges."""

import asyncio
import csv
import io

import httpx
import pytest
from apps.agents import action_center
from apps.agents.action_center import router
from apps.security import authentication as auth
from database import get_db
from fastapi import FastAPI
from models import Base, Case
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


@pytest.mark.parametrize(
    "params",
    [
        {"date_from": "2026-02-30"},
        {"date_to": "20261001"},
        {"date_from": "2026-10-02", "date_to": "2026-10-01"},
    ],
)
def test_action_center_rejects_invalid_date_filters(params):
    async def run():
        app = FastAPI()
        app.include_router(router)

        async def identity():
            return {"user": "finance@example.test", "role": "finance"}

        async def unused_database():
            yield None

        app.dependency_overrides[auth.current_user] = identity
        app.dependency_overrides[get_db] = unused_database
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/action-center/cases", params=params)
        assert response.status_code == 422

    asyncio.run(run())


def test_action_center_rejects_unbounded_page_numbers():
    async def run():
        app = FastAPI()
        app.include_router(router)

        async def identity():
            return {"user": "finance@example.test", "role": "finance"}

        async def unused_database():
            yield None

        app.dependency_overrides[auth.current_user] = identity
        app.dependency_overrides[get_db] = unused_database
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/action-center/cases", params={"page": 10_001})
        assert response.status_code == 422

    asyncio.run(run())


@pytest.mark.parametrize(
    "params",
    [
        {"sort": "unknown"},
        {"dir": "sideways"},
        {"store": ",".join(f"S{i}" for i in range(101))},
        {"store": "S" * 51},
    ],
)
def test_action_center_rejects_invalid_query_options(params):
    async def run():
        app = FastAPI()
        app.include_router(router)

        async def identity():
            return {"user": "finance@example.test", "role": "finance"}

        async def unused_database():
            yield None

        app.dependency_overrides[auth.current_user] = identity
        app.dependency_overrides[get_db] = unused_database
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/action-center/cases", params=params)
        assert response.status_code == 422

    asyncio.run(run())


@pytest.mark.parametrize(
    "params",
    [
        {"stage": "unknown"},
        {"sla": "late"},
        {"snoozed": "yes"},
        {"min_amount": "NaN"},
        {"max_amount": "Infinity"},
        {"min_amount": "20", "max_amount": "10"},
    ],
)
def test_action_center_rejects_unsupported_or_inconsistent_filters(params):
    async def run():
        app = FastAPI()
        app.include_router(router)

        async def identity():
            return {"user": "finance@example.test", "role": "finance"}

        async def unused_database():
            yield None

        app.dependency_overrides[auth.current_user] = identity
        app.dependency_overrides[get_db] = unused_database
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/action-center/cases", params=params)
        assert response.status_code == 422

    asyncio.run(run())


def test_action_center_accepts_supported_filter_values():
    from apps.agents.action_center import filters

    supported = filters(
        store="STORE-1", stage="open", sla="at_risk", snoozed="only", min_amount=1, max_amount=10
    )
    assert supported.store == "STORE-1" and supported.stage == "open"


def test_action_center_export_refuses_to_return_a_silent_partial_file(monkeypatch):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with sessions() as db:
                db.add_all(
                    [
                        Case(
                            id=f"case-{index}",
                            case_number=f"CASE-{index}",
                            case_type="Test",
                            business_date="2026-10-01",
                            store_id="S1",
                        )
                        for index in range(3)
                    ]
                )
                await db.commit()

            app = FastAPI()
            app.include_router(action_center.router)

            async def identity():
                return {"user": "finance@example.test", "role": "finance"}

            async def database():
                async with sessions() as db:
                    yield db

            app.dependency_overrides[auth.current_user] = identity
            app.dependency_overrides[get_db] = database
            monkeypatch.setattr(action_center, "MAX_ACTION_CENTER_EXPORT_CASES", 2)

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/action-center/export.csv")

            assert response.status_code == 413
            assert "Export contains 3 cases" in response.json()["detail"]
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_action_center_csv_export_neutralizes_spreadsheet_formulas():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with sessions() as db:
                db.add(
                    Case(
                        id="case-formula",
                        case_number="=1+1",
                        case_type="+SUM(A1:A2)",
                        business_date="2026-10-01",
                        store_id="S1",
                    )
                )
                await db.commit()

            app = FastAPI()
            app.include_router(action_center.router)

            async def identity():
                return {"user": "finance@example.test", "role": "finance"}

            async def database():
                async with sessions() as db:
                    yield db

            app.dependency_overrides[auth.current_user] = identity
            app.dependency_overrides[get_db] = database
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/action-center/export.csv")

            assert response.status_code == 200
            rows = list(csv.reader(io.StringIO(response.text)))
            case_number_column = rows[0].index("case_number")
            case_type_column = rows[0].index("case_type")
            assert rows[1][case_number_column] == "'=1+1"
            assert rows[1][case_type_column] == "'+SUM(A1:A2)"
        finally:
            await engine.dispose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "select_all",
    [
        {"date_from": "2026-02-30"},
        {"date_from": "2026-10-02", "date_to": "2026-10-01"},
    ],
)
def test_bulk_select_all_uses_the_same_date_validation(select_all):
    async def run():
        app = FastAPI()
        app.include_router(router)

        async def identity():
            return {"user": "finance@example.test", "role": "finance"}

        async def unused_database():
            yield None

        app.dependency_overrides[auth.current_user] = identity
        app.dependency_overrides[get_db] = unused_database
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/action-center/bulk",
                json={"action": "assign", "select_all": select_all},
            )
        assert response.status_code == 422

    asyncio.run(run())
