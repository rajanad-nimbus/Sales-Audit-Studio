"""Use Ontology Studio's actual envelope, release pins and cursor protocol."""

import asyncio

import httpx
import pytest

from integrations.ontology.ontology_integration import RULE_CODES, OntologyIntegration

META = {
    "workspaceId": 7,
    "releaseId": 12,
    "approved": True,
    "version": "1.0",
    "contentHash": "abc",
    "releaseHash": "abc",
}


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setenv("NIMBUS_ONTOLOGY_API_URL", "https://ontology.test/api/integration/v1")
    monkeypatch.setenv("NIMBUS_ONTOLOGY_API_KEY", "test-key")


def response(request, meta=None, rows=None, cursor=None):
    if rows is None:
        rows = (
            [{"ruleCode": code, "expression": "governed control"} for code in sorted(RULE_CODES)]
            if request.url.path.endswith("business-rules")
            else []
        )
    return httpx.Response(
        200, json={"ontology": meta or META, "data": rows, "pagination": {"nextCursor": cursor}}
    )


def test_complete_context_and_pagination_are_pinned():
    calls = []

    def handle(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer test-key"
        assert request.url.params["requireApproved"] == "true"
        if len(calls) > 1:
            assert request.url.params["releaseId"] == "12"
        if request.url.path.endswith("business-rules") and "cursor" not in request.url.params:
            return response(request, rows=[], cursor="next")
        return response(request)

    async def go():
        integration = OntologyIntegration()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            assert await integration.refresh(client) == RULE_CODES
        assert integration.status()["ready"]
        assert len(calls) == 7
        copy = integration.context()
        copy["ontology"]["releaseId"] = 99
        assert integration.context()["ontology"]["releaseId"] == 12

    asyncio.run(go())


@pytest.mark.parametrize(
    "failure", ["unapproved", "mixed", "missing", "cursor", "revoked", "malformed"]
)
def test_invalid_refresh_discards_previous_active_context(failure):
    def bad(request):
        if failure == "revoked":
            return httpx.Response(401)
        if failure == "malformed":
            return httpx.Response(200, json=[])
        if failure == "unapproved":
            return response(request, meta={**META, "approved": False})
        if failure == "mixed" and request.url.path.endswith("entities"):
            return response(request, meta={**META, "releaseId": 13})
        if failure == "missing":
            return response(request, rows=[])
        if failure == "cursor":
            return response(request, cursor="repeated")
        return response(request)

    async def go():
        integration = OntologyIntegration()
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            await integration.refresh(client)
        assert integration.status()["ready"]
        async with httpx.AsyncClient(transport=httpx.MockTransport(bad)) as client:
            assert await integration.refresh(client) == frozenset()
        status = integration.status()
        assert not status["ready"]
        assert status["source"] == "built-in-fallback"
        assert status["last_success_at"] is not None
        assert status["last_error"]
        assert not integration.context()["definitions"]

    asyncio.run(go())


@pytest.mark.parametrize("declared_length", [True, False])
def test_oversized_ontology_page_is_rejected_before_full_buffering(monkeypatch, declared_length):
    read_chunks = []

    class ChunkedBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            read_chunks.append(1)
            yield b"x" * 12
            read_chunks.append(2)
            yield b"y" * 12

        async def aclose(self):
            pass

    async def go():
        monkeypatch.setattr(
            "integrations.ontology.ontology_integration.MAX_ONTOLOGY_RESPONSE_BYTES", 16
        )

        def oversized(_request):
            return httpx.Response(
                200,
                content=b"x" * 17 if declared_length else None,
                stream=None if declared_length else ChunkedBody(),
            )

        integration = OntologyIntegration()
        async with httpx.AsyncClient(transport=httpx.MockTransport(oversized)) as client:
            assert await integration.refresh(client) == frozenset()
        assert integration.status()["last_error"] == "Ontology context refresh failed: ValueError"
        assert not integration.context()["definitions"]
        assert read_chunks == ([] if declared_length else [1, 2])

    asyncio.run(go())


def test_unconfigured_is_explicit_fallback(monkeypatch):
    monkeypatch.delenv("NIMBUS_ONTOLOGY_API_KEY")
    integration = OntologyIntegration()
    assert asyncio.run(integration.refresh()) == frozenset()
    assert integration.provenance() == {"source": "built-in-fallback", "ontology": None}


def test_database_bindings_cover_audit_and_case_relationships():
    from integrations.ontology.ontology_bindings import database_bindings

    context = {"definitions": {"entities": [{"id": 2, "name": "SalesAuditCase"}]}}
    bindings = {b["model"]: b for b in database_bindings(context)}
    assert bindings["Case"]["ontology_entity_id"] == 2
    assert bindings["AuditEvent"]["status"] == "not-visible-in-release"
    columns = {c["name"]: c for c in bindings["AuditEvent"]["columns"]}
    assert columns["case_id"]["references"] == ["cases.id"]
    assert {"hash", "prev_hash", "after_state"} <= columns.keys()


def test_audit_preserves_pinned_run_context():
    from types import SimpleNamespace

    from integrations.ontology import nimbus

    events = []
    context = {"source": "ontology-integration", "ontology": META}
    nimbus.audit(
        SimpleNamespace(add=events.append),
        "AGENT_RUN",
        "c1",
        "Case",
        "c1",
        "run",
        actor="investigation-agent",
        after={"ontology_context": context, "result": None},
    )
    assert events[0].after_state["ontology_context"] == context
    assert "result" in events[0].after_state


def test_prompt_includes_governed_rules_without_changing_tool_permissions():
    from apps.agents.investigator import ontology_prompt

    assert ontology_prompt(None) == ""
    prompt = ontology_prompt(
        {
            "ontology": META,
            "definitions": {
                "business-rules": [
                    {"ruleCode": "NSA-EVIDENCE-REQUIRED", "expression": "Require evidence"}
                ],
                "workflows": [{"code": "NSA-CASE-LIFECYCLE"}],
            },
        }
    )
    assert "Require evidence" in prompt and "NSA-CASE-LIFECYCLE" in prompt
    assert "never overrides tool scope" in prompt
