import asyncio

import httpx
import pytest

from apps.security import authentication as auth
import main
from apps.security import middleware


@pytest.mark.parametrize("path,maximum", [
    ("/api/cases", 500),
    ("/api/audit", 500),
    ("/api/transaction-adjustments/transactions/search?q=a", 25),
    ("/api/operations/retention/runs", 200),
    ("/api/batch/runs", 100),
    ("/api/ingest/deliveries", 100),
    ("/api/store-days", 500),
])
@pytest.mark.parametrize("boundary", ["negative", "zero", "excessive"])
def test_list_limits_reject_invalid_values(monkeypatch, path, maximum, boundary):
    async def identity():
        return {"user": "admin@example.test", "role": "admin"}

    async def middleware_identity(_request, _authorization):
        return await identity()

    monkeypatch.setattr(middleware, "current_user", middleware_identity)
    monkeypatch.setitem(main.app.dependency_overrides, auth.current_user, identity)
    limit = {"negative": -1, "zero": 0, "excessive": maximum + 1}[boundary]
    separator = "&" if "?" in path else "?"

    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
            return await client.get(f"{path}{separator}limit={limit}")

    assert asyncio.run(request()).status_code == 422


def test_cases_reject_negative_skip(monkeypatch):
    async def identity(_request, _authorization):
        return {"user": "admin@example.test", "role": "admin"}

    monkeypatch.setattr(middleware, "current_user", identity)

    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
            return await client.get("/api/cases?skip=-1")

    assert asyncio.run(request()).status_code == 422
