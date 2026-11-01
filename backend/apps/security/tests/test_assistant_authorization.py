import asyncio

import httpx
import main
import pytest

from apps.agents import llm
from apps.assistant import chat
from apps.security import authentication as auth
from apps.security import middleware


@pytest.mark.parametrize(
    "role,allowed",
    [
        ("viewer", False),
        ("store_manager", False),
        ("it", False),
        ("finance", True),
        ("analyst", True),
        ("auditor", True),
        ("admin", True),
    ],
)
def test_assistant_only_accessible_to_roles_allowed_case_context(monkeypatch, role, allowed):
    calls = []

    async def identity():
        return {
            "user": "user@example.test",
            "role": role,
            "store_scoped": role == "store_manager",
            "stores": [],
        }

    async def middleware_identity(_request, _authorization):
        return await identity()

    async def context(_db, _case_id, _message, _user):
        calls.append("context loaded")
        return {"selected_case": None, "needs_decision": []}

    async def narrator(*_args):
        return None

    monkeypatch.setattr(middleware, "current_user", middleware_identity)
    monkeypatch.setitem(main.app.dependency_overrides, auth.current_user, identity)
    monkeypatch.setattr(chat, "build_context", context)
    monkeypatch.setattr(llm, "chat", narrator)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test"
        ) as client:
            return await client.post("/api/chat", json={"message": "hello"})

    response = asyncio.run(run())
    assert response.status_code == (200 if allowed else 403)
    assert calls == (["context loaded"] if allowed else [])
