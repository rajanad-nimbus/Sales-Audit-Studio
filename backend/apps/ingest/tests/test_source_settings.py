"""Per-source switches: defaults, the batch filter and the refusal of a switched-off source."""
import asyncio
from types import SimpleNamespace

import pytest
from apps.ingest import source_settings as ss
from fastapi import HTTPException


def row(**kw):
    return SimpleNamespace(**{"enabled": True, "in_daily_batch": True, "manual_sync": True, "sync_frequency": None,
                              "sync_time": None, "sync_weekday": None, "sync_timezone": None, "last_scheduled_run_at": None, "last_scheduled_status": None, "last_scheduled_message": None, **kw})


def test_a_source_without_a_row_behaves_as_before(monkeypatch):
    for name in ("SHOPIFY_STORE_URL", "SHOPIFY_ACCESS_TOKEN", "SHOPIFY_API_KEY", "SHOPIFY_API_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    pos = ss.effective("POS", None)
    assert (pos["enabled"], pos["in_daily_batch"], pos["manual_sync"], pos["sync_frequency"]) == (True, True, True, None)
    assert ss.effective("Shopify", None)["in_daily_batch"] is False   # not configured, so the batch skips it
    monkeypatch.setenv("SHOPIFY_STORE_URL", "https://x.myshopify.com")
    assert ss.effective("Shopify", None)["in_daily_batch"] is False
    monkeypatch.setenv("SHOPIFY_ACCESS_TOKEN", "token")
    assert ss.effective("Shopify", None)["in_daily_batch"] is True


def test_shopify_readiness_requires_a_store_and_a_supported_authentication_method(monkeypatch):
    for name in ("SHOPIFY_STORE_URL", "SHOPIFY_ACCESS_TOKEN", "SHOPIFY_API_KEY", "SHOPIFY_API_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    assert not ss.shopify_configured()
    monkeypatch.setenv("SHOPIFY_STORE_URL", "https://x.myshopify.com")
    assert not ss.shopify_configured()
    monkeypatch.setenv("SHOPIFY_API_KEY", "key")
    assert not ss.shopify_configured()
    monkeypatch.setenv("SHOPIFY_API_PASSWORD", "password")
    assert ss.shopify_configured()
    monkeypatch.delenv("SHOPIFY_API_KEY")
    monkeypatch.delenv("SHOPIFY_API_PASSWORD")
    monkeypatch.setenv("SHOPIFY_ACCESS_TOKEN", "token")
    assert ss.shopify_configured()


def test_a_saved_row_wins_over_the_default(monkeypatch):
    monkeypatch.setenv("SHOPIFY_STORE_URL", "https://x.myshopify.com")
    monkeypatch.setenv("SHOPIFY_ACCESS_TOKEN", "token")
    assert ss.effective("Shopify", row(in_daily_batch=False))["in_daily_batch"] is False


class FakeDb:
    """Stands in for the session: load() reads rows through execute().scalars().all()."""
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, _):
        rows = self.rows
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))


def settings_for(**by_source):
    return FakeDb([row(source_system=s, **v) for s, v in by_source.items()])


def test_batch_sources_leaves_out_switched_off_and_unbatched_sources(monkeypatch):
    monkeypatch.setenv("SHOPIFY_STORE_URL", "https://x.myshopify.com")
    monkeypatch.setenv("SHOPIFY_ACCESS_TOKEN", "token")
    db = settings_for(Bank={"enabled": False}, ERP={"in_daily_batch": False})
    assert asyncio.run(ss.batch_sources(db)) == {"POS", "POSControl", "Processor", "Shopify"}


def test_a_switched_off_source_is_refused_everywhere():
    db = settings_for(Shopify={"enabled": False})
    with pytest.raises(HTTPException) as e:
        asyncio.run(ss.require_allowed(db, "Shopify"))
    assert e.value.status_code == 409 and "switched off" in e.value.detail


def test_manual_sync_can_be_closed_while_the_source_stays_on():
    db = settings_for(Shopify={"manual_sync": False})
    asyncio.run(ss.require_allowed(db, "Shopify"))                      # a scheduled or webhook path still works
    with pytest.raises(HTTPException) as e:
        asyncio.run(ss.require_allowed(db, "Shopify", manual=True))
    assert "Manual sync" in e.value.detail
