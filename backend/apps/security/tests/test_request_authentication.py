import asyncio

import httpx
import main
from fastapi import Depends, FastAPI

from apps.security import authentication as auth


def test_request_authenticates_once_and_dependencies_use_same_identity(monkeypatch):
    calls = []
    real_client = httpx.AsyncClient

    class IdentityClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def get(self, url, headers):
            calls.append((url, headers))
            return httpx.Response(200, json={"user": {"email": "finance@example.test", "role": "finance"}})

    monkeypatch.setattr(auth, "ONTOLOGY_AUTH_URL", "https://identity.example/auth/me")
    monkeypatch.setattr(auth.httpx, "AsyncClient", IdentityClient)

    async def run():
        async with real_client(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
            response = await client.get("/api/me", headers={"Authorization": "Bearer token"})
        assert response.status_code == 200
        assert response.json()["user"] == "finance@example.test"
        assert response.json()["role"] == "finance"

    asyncio.run(run())
    assert len(calls) == 1
    assert calls[0][1] == {"Authorization": "Bearer token"}


def test_dependency_authenticates_when_security_middleware_is_absent(monkeypatch):
    calls = []
    real_client = httpx.AsyncClient

    class IdentityClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def get(self, _url, headers):
            calls.append(headers)
            return httpx.Response(200, json={"user": {"email": "viewer@example.test", "role": "viewer"}})

    app = FastAPI()

    @app.get("/identity")
    async def identity(user: dict = Depends(auth.current_user)):
        return user

    monkeypatch.setattr(auth, "ONTOLOGY_AUTH_URL", "https://identity.example/auth/me")
    monkeypatch.setattr(auth.httpx, "AsyncClient", IdentityClient)

    async def run():
        async with real_client(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/identity", headers={"Authorization": "Bearer token"})
        assert response.status_code == 200
        assert response.json()["role"] == "viewer"

    asyncio.run(run())
    assert len(calls) == 1
