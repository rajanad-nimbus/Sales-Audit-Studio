"""Derived role permissions (analyst, auditor, store_manager) from existing require_role declarations."""
from auth import role_allowed


def test_analyst_does_shared_work_but_not_approvals_or_it_ops():
    assert role_allowed("analyst", ("finance", "it"), "POST", "/api/cases/1/investigate")
    assert not role_allowed("analyst", ("finance",), "POST", "/api/corrections/1/approve")
    assert not role_allowed("analyst", ("it",), "POST", "/api/batch/run")


def test_auditor_is_read_only():
    assert role_allowed("auditor", ("finance", "it"), "GET", "/api/cases")
    assert role_allowed("auditor", ("it",), "GET", "/api/batch/runs")
    assert not role_allowed("auditor", ("finance", "it"), "POST", "/api/cases/1/investigate")
    assert not role_allowed("auditor", ("finance",), "POST", "/api/corrections/1/approve")


def test_store_manager_limited_to_cash_and_store_days():
    assert role_allowed("store_manager", ("finance", "it"), "POST", "/api/cash")
    assert role_allowed("store_manager", ("finance", "it"), "GET", "/api/store-days/S1/2026-10-01")
    assert not role_allowed("store_manager", ("finance", "it"), "POST", "/api/store-days/S1/2026-10-01/retotal")
    assert not role_allowed("store_manager", ("finance", "it"), "GET", "/api/cases")
    assert not role_allowed("store_manager", ("finance",), "POST", "/api/cash")


def test_finance_it_admin_unchanged():
    assert role_allowed("finance", ("finance",), "POST", "/x")
    assert not role_allowed("finance", ("it",), "POST", "/x")
    assert role_allowed("admin", ("it",), "POST", "/x")
    assert not role_allowed("viewer", ("finance", "it"), "GET", "/x")


def test_agent_settings_validation_rejects_unsafe_changes():
    import pytest
    from fastapi import HTTPException
    import agent_settings as s
    assert s.validate({"model": "claude-opus-5-5", "tools": ["get_source_record"]})["tools"] == ["get_source_record"]
    for bad in ({"model": "gpt-x"}, {"tools": []}, {"tools": ["delete_everything"]},
                {"investigator_enabled": "yes"}, {"autonomy_limit": 99999}):
        with pytest.raises(HTTPException):
            s.validate(bad)


def test_chat_via_agent_sdk_is_read_only_and_uses_result(monkeypatch):
    import asyncio
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock
    import llm
    seen = {}

    async def fake_query(prompt, options):
        seen["options"], seen["prompt"] = options, prompt
        yield AssistantMessage(content=[TextBlock(text="draft")], model="m")
        yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1,
                            session_id="s", result="final answer")

    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok")
    out = asyncio.run(llm.chat_via_agent_sdk("SYS", [{"role": "user", "content": "hi"}], "q?", query_fn=fake_query))
    assert out == "final answer"
    o = seen["options"]
    assert o.tools == [] and o.allowed_tools == [] and o.max_turns == 1 and o.setting_sources == []
    assert "User: q?" in seen["prompt"] and "User: hi" in seen["prompt"]
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN")
    assert asyncio.run(llm.chat_via_agent_sdk("SYS", [], "q?", query_fn=fake_query)) is None
