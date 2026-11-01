"""Optional investigation narrative generation falls back without changing audit provenance."""

import asyncio
from types import SimpleNamespace

from apps.agents import investigation_service


def test_successful_agent_narrative_is_audited_without_llm_fallback(monkeypatch):
    calls = []

    async def load_settings(_db):
        return {"investigator_enabled": True, "model": "test-model", "tools": ["list_transactions"]}

    async def run_agent(*_args, **kwargs):
        calls.append(("agent", kwargs))
        return {"result": {"summary": "Grounded finding"}, "trace": {"tool_calls": []}}

    async def unexpected_fallback(*_args):
        raise AssertionError("successful agent output should not invoke the fallback")

    def render(_result):
        return "Rendered grounded finding"

    def audit(*args, **kwargs):
        calls.append(("audit", args, kwargs))

    monkeypatch.setattr(investigation_service.agent_settings, "load", load_settings)
    monkeypatch.setattr(investigation_service.investigator, "run", run_agent)
    monkeypatch.setattr(investigation_service.investigator, "render", render)
    monkeypatch.setattr(investigation_service.llm, "narrate", unexpected_fallback)
    monkeypatch.setattr(investigation_service.nimbus, "audit", audit)

    async def run():
        return await investigation_service._investigation_narrative(
            None,
            SimpleNamespace(id="case-1", total_exception_amount=12),
            [SimpleNamespace(exception_type="UNMATCHED_SALE")],
            True,
            {"source": "test", "ontology": "v1"},
            {
                "label": "Unmatched Sale",
                "known_causes": ["missing tender"],
                "evidence": ["POS journal"],
            },
        )

    assert asyncio.run(run()) == "Rendered grounded finding"
    assert calls[0][0] == "agent"
    assert calls[0][1]["model"] == "test-model"
    assert calls[1][0] == "audit"
    assert calls[1][2]["after"]["ontology_context"] == {"source": "test", "ontology": "v1"}


def test_failed_agent_narrative_uses_labeled_llm_fallback(monkeypatch):
    async def load_settings(_db):
        return {"investigator_enabled": True, "model": "test-model", "tools": []}

    async def run_agent(*_args, **_kwargs):
        return {"result": None, "trace": {"error": "unavailable"}}

    async def narrate(label, amount, evidence, causes):
        assert (label, amount, evidence, causes) == (
            "Unmatched Sale",
            "12",
            ["POS journal"],
            ["missing tender"],
        )
        return "Careful narrative"

    audits = []
    monkeypatch.setattr(investigation_service.agent_settings, "load", load_settings)
    monkeypatch.setattr(investigation_service.investigator, "run", run_agent)
    monkeypatch.setattr(investigation_service.llm, "narrate", narrate)
    monkeypatch.setattr(
        investigation_service.nimbus, "audit", lambda *args, **kwargs: audits.append((args, kwargs))
    )

    async def run():
        return await investigation_service._investigation_narrative(
            None,
            SimpleNamespace(id="case-1", total_exception_amount=12),
            [SimpleNamespace(exception_type="UNMATCHED_SALE")],
            True,
            {"source": "test", "ontology": "v1"},
            {
                "label": "Unmatched Sale",
                "known_causes": ["missing tender"],
                "evidence": ["POS journal"],
            },
        )

    narrative = asyncio.run(run())
    assert narrative == "[LLM-drafted; figures and authority are deterministic] Careful narrative"
    assert audits[0][0][1] == "AGENT_RUN"
    assert audits[0][0][5] == "Investigation agent run failed"


def test_disabled_agent_returns_no_narrative_when_fallback_is_empty(monkeypatch):
    async def load_settings(_db):
        return {"investigator_enabled": False, "model": "test-model", "tools": []}

    async def unexpected_agent(*_args, **_kwargs):
        raise AssertionError("disabled investigator must not run")

    async def empty_narrative(*_args):
        return None

    monkeypatch.setattr(investigation_service.agent_settings, "load", load_settings)
    monkeypatch.setattr(investigation_service.investigator, "run", unexpected_agent)
    monkeypatch.setattr(investigation_service.llm, "narrate", empty_narrative)

    async def run():
        return await investigation_service._investigation_narrative(
            None,
            SimpleNamespace(id="case-1", total_exception_amount=12),
            [SimpleNamespace(exception_type="UNMATCHED_SALE")],
            True,
            {"source": "test", "ontology": "v1"},
            {"label": "Unmatched Sale", "known_causes": [], "evidence": []},
        )

    assert asyncio.run(run()) is None
