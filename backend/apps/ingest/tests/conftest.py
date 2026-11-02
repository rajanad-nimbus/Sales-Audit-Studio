import pytest


@pytest.fixture(autouse=True)
def pinned_shopify_api_version(monkeypatch):
    """The mock Shopify API serves 2024-01; keep a developer's .env version out of these tests."""
    monkeypatch.setenv("SHOPIFY_API_VERSION", "2024-01")
