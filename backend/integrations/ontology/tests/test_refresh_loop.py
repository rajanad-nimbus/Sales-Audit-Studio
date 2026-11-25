"""A newly approved ontology release must reach the running server without a restart."""

import asyncio

import pytest

from integrations.ontology import ontology_integration as module


class FakeIntegration:
    def __init__(self, releases):
        self.releases = list(releases)
        self.calls = 0
        self.current = None

    async def refresh(self):
        self.calls += 1
        step = self.releases[min(self.calls - 1, len(self.releases) - 1)]
        if isinstance(step, Exception):
            raise step
        self.current = step

    def provenance(self):
        return {"ontology": {"releaseId": self.current} if self.current else None}


def run_for(target, seconds=0.12, interval=0.01):
    async def go():
        task = asyncio.create_task(module.refresh_loop(target, interval))
        await asyncio.sleep(seconds)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())


def test_a_release_approved_while_running_is_picked_up_on_a_later_tick():
    target = FakeIntegration([20, 20, 21])
    run_for(target)
    assert target.current == 21 and target.calls >= 3


def test_a_failed_refresh_does_not_stop_the_loop_and_the_next_tick_recovers():
    target = FakeIntegration([20, RuntimeError("ontology unreachable"), 21])
    run_for(target)
    assert target.current == 21


def test_the_loop_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("NIMBUS_SCHEDULER", "0")
    assert module.refresh_loop_enabled() is False
    monkeypatch.setenv("NIMBUS_SCHEDULER", "1")
    monkeypatch.setattr(module, "REFRESH_INTERVAL_SECONDS", 0.0)
    assert module.refresh_loop_enabled() is False
    monkeypatch.setattr(module, "REFRESH_INTERVAL_SECONDS", 300.0)
    assert module.refresh_loop_enabled() is True
