import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from apps.system import lifecycle


def test_database_is_disposed_when_application_exits_with_error(monkeypatch):
    events = []

    async def refresh():
        events.append("refresh")

    async def provision(_db):
        events.append("provision")

    async def dispose():
        events.append("dispose")

    monkeypatch.setattr(lifecycle.ontology_integration, "refresh", refresh)
    monkeypatch.setattr(lifecycle, "ensure_default_connectors", provision)
    monkeypatch.setattr(lifecycle, "engine", SimpleNamespace(dispose=dispose))

    async def run():
        async with lifecycle.lifespan(FastAPI()):
            raise RuntimeError("application error")

    with pytest.raises(RuntimeError, match="application error"):
        asyncio.run(run())
    assert events == ["provision", "refresh", "dispose"]


def test_shared_http_clients_are_closed_and_removed_from_app_state(monkeypatch):
    async def refresh():
        return frozenset()

    async def provision(_db):
        return None

    async def dispose():
        return None

    monkeypatch.setattr(lifecycle.ontology_integration, "refresh", refresh)
    monkeypatch.setattr(lifecycle, "ensure_default_connectors", provision)
    monkeypatch.setattr(lifecycle, "engine", SimpleNamespace(dispose=dispose))

    async def run():
        app = FastAPI()
        async with lifecycle.lifespan(app):
            identity_client = app.state.identity_http_client
            export_client = app.state.export_http_client
            assert not identity_client.is_closed
            assert not export_client.is_closed
        assert identity_client.is_closed
        assert export_client.is_closed
        assert not hasattr(app.state, "identity_http_client")
        assert not hasattr(app.state, "export_http_client")

    asyncio.run(run())
