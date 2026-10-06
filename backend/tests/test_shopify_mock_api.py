"""Run the real adapter against a local mock of the Shopify Admin REST API."""
import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

import shopify_adapter as sa


def make_app(state):
    orders = [{"id": i, "created_at": "2024-01-15T10:00:00Z", "total_price": "10.00", "currency": "USD",
               "financial_status": "partially_refunded" if i == 3 else "paid", "customer": None}
              for i in range(1, 6)]

    async def list_orders(request):
        state["order_calls"].append(dict(request.query))
        state["auth"].append(request.headers.get("X-Shopify-Access-Token"))
        if state.get("fail_first") and len(state["order_calls"]) == 1:
            return web.Response(status=429, headers={"Retry-After": "0"})
        if "page_info" in request.query:  # Shopify rejects filters alongside page_info
            assert "created_at_min" not in request.query
            start = int(request.query["page_info"])
        else:
            start = 0
        limit = int(request.query["limit"])
        page = orders[start:start + limit]
        headers = {}
        if start + limit < len(orders):
            url = f"{request.scheme}://{request.host}/admin/api/2024-01/orders.json?limit={limit}&page_info={start + limit}"
            headers["Link"] = f'<{url}>; rel="next"'
        return web.json_response({"orders": page}, headers=headers)

    async def list_refunds(request):
        state["refund_calls"].append(request.match_info["oid"])
        return web.json_response({"refunds": [{"id": 900, "order_id": 3, "created_at": "2024-01-16T09:00:00Z",
                                               "transactions": [{"kind": "refund", "status": "success",
                                                                 "amount": "4.00", "currency": "USD"}]}]})

    app = web.Application()
    app.router.add_get("/admin/api/2024-01/orders.json", list_orders)
    app.router.add_get("/admin/api/2024-01/orders/{oid}/refunds.json", list_refunds)
    return app


def test_feed_paginates_retries_and_fetches_refunds_only_when_needed(monkeypatch):
    state = {"order_calls": [], "refund_calls": [], "auth": [], "fail_first": True}

    async def go():
        server = TestServer(make_app(state))
        await server.start_server()
        try:
            monkeypatch.setenv("SHOPIFY_STORE_URL", f"http://{server.host}:{server.port}")
            monkeypatch.setenv("SHOPIFY_ACCESS_TOKEN", "tok")
            monkeypatch.setenv("SHOPIFY_FETCH_LIMIT", "2")
            return await sa.fetch_shopify_feed("S1")
        finally:
            await server.close()

    feed = asyncio.run(go())
    recs = feed["Shopify"]
    assert [r["source_record_id"] for r in recs] == [
        "SHOP-1", "SHOP-2", "SHOP-3", "SHREF-900", "SHOP-4", "SHOP-5"]
    assert state["refund_calls"] == ["3"]          # only the partially_refunded order
    assert len(state["order_calls"]) == 4           # 1 throttled + 3 pages
    assert set(state["auth"]) == {"tok"}
    refund = recs[3]
    assert refund["amount"] == "4.00" and refund["payment_reference"] == "3"


def test_api_error_propagates():
    async def go():
        async def boom(request):
            return web.Response(status=401, text="bad token")
        app = web.Application()
        app.router.add_get("/admin/api/2024-01/orders.json", boom)
        server = TestServer(app)
        await server.start_server()
        try:
            adapter = sa.ShopifyAdapter(store_url=f"http://{server.host}:{server.port}", access_token="x")
            with pytest.raises(sa.ShopifyAPIError, match="401"):
                async for _ in adapter.fetch_orders():
                    pass
            await adapter.close()
        finally:
            await server.close()
    asyncio.run(go())
