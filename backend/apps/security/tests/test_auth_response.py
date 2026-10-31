import asyncio

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

import auth


@pytest.mark.parametrize("user", [None, [], "invalid", 42])
def test_malformed_identity_fails_closed(monkeypatch, user):
    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def get(self, _url, headers):
            return httpx.Response(200, json={"user": user})

    monkeypatch.setattr(auth, "ONTOLOGY_AUTH_URL", "https://identity.example")
    monkeypatch.setattr(auth.httpx, "AsyncClient", Client)
    request = Request({"type": "http", "method": "GET", "path": "/api/cases", "headers": []})

    with pytest.raises(HTTPException) as exc:
        asyncio.run(auth.current_user(request, "Bearer token"))

    assert exc.value.status_code == 503
