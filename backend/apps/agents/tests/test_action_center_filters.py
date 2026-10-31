"""Action Center filters reject malformed or reversed calendar-date ranges."""

import asyncio

import httpx
import pytest
from fastapi import FastAPI

import auth
from apps.agents.action_center import router
from database import get_db


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


@pytest.mark.parametrize("params", [
    {"sort": "unknown"},
    {"dir": "sideways"},
    {"store": ",".join(f"S{i}" for i in range(101))},
    {"store": "S" * 51},
])
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


def test_action_center_accepts_supported_filter_values():
    from apps.agents.action_center import filters

    assert filters(store="STORE-1").store == "STORE-1"


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
