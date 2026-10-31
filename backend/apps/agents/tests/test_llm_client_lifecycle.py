import asyncio
import sys
from types import SimpleNamespace

from apps.agents import investigator, llm


class FakeAnthropicClient:
    instances = []

    def __init__(self, **_kwargs):
        self.closed = False
        self.messages = SimpleNamespace(create=self.create)
        self.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        await self.close()

    async def close(self):
        self.closed = True

    async def create(self, **_kwargs):
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="Grounded draft")],
                               usage=SimpleNamespace(input_tokens=1, output_tokens=1))


def configure_fake_provider(monkeypatch):
    FakeAnthropicClient.instances.clear()
    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(AsyncAnthropic=FakeAnthropicClient))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)


def test_chat_and_narration_close_owned_api_clients(monkeypatch):
    configure_fake_provider(monkeypatch)

    async def run():
        narrative = await llm.narrate("Amount mismatch", "12.00", [], [])
        answer = await llm.chat({}, [], "Summarize the evidence")
        assert narrative == "Grounded draft"
        assert answer == "Grounded draft"

    asyncio.run(run())
    assert len(FakeAnthropicClient.instances) == 2
    assert all(client.closed for client in FakeAnthropicClient.instances)


def test_investigator_closes_only_clients_it_creates(monkeypatch):
    configure_fake_provider(monkeypatch)

    async def run():
        result = await investigator.run(
            db=None,
            case=SimpleNamespace(case_number="CASE-1", store_id="S1", business_date="2026-10-01"),
            exceptions=[],
            label="Amount mismatch",
            known_causes=[],
        )
        assert result["result"] is None

    asyncio.run(run())
    assert len(FakeAnthropicClient.instances) == 1
    assert FakeAnthropicClient.instances[0].closed
