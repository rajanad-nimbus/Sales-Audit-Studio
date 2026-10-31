import asyncio

import httpx
import pytest

from apps.security import authentication as auth
import main
from apps.security import middleware


@pytest.mark.parametrize(
    "path,payload",
    [
        ("/api/findings", {"case_id": "c", "investigation_id": "i", "conclusion": "x",
                           "finding_type": "x", "confidence": "Low"}),
        ("/api/recommendations", {"case_id": "c", "investigation_id": "i", "disposition_type": "x",
                                  "action_class": "x", "confidence": "Low"}),
    ],
)
def test_auditor_cannot_create_case_findings_or_recommendations(monkeypatch, path, payload):
    async def auditor():
        return {"user": "audit@example.test", "role": "auditor"}

    async def middleware_auditor(_request, _authorization):
        return await auditor()

    monkeypatch.setattr(middleware, "current_user", middleware_auditor)
    monkeypatch.setitem(main.app.dependency_overrides, auth.current_user, auditor)

    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
            return await client.post(path, json=payload)

    response = asyncio.run(request())
    assert response.status_code == 403
