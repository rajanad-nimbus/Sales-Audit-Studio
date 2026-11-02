"""Run the real adapter against a local mock of the Shopify Admin REST API."""

import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from apps.ingest.shopify import adapter as sa
from multidict import CIMultiDict


def make_app(state):
    orders = [
        {
            "id": i,
            "created_at": "2024-01-15T10:00:00Z",
            "total_price": "10.00",
            "currency": "USD",
            "financial_status": "partially_refunded" if i == 3 else "paid",
            "customer": None,
        }
        for i in range(1, 6)
    ]

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
        page = orders[start : start + limit]
        headers = {}
        if start + limit < len(orders):
            url = f"{request.scheme}://{request.host}/admin/api/2024-01/orders.json?limit={limit}&page_info={start + limit}"
            headers["Link"] = f'<{url}>; rel="next"'
        return web.json_response({"orders": page}, headers=headers)

    async def list_refunds(request):
        state["refund_calls"].append(request.match_info["oid"])
        cursor = request.query.get("page_info")
        state.setdefault("refund_cursors", []).append(cursor)
        if cursor is not None:
            assert cursor == "next+page"
            if state.get("fail_second_refund_page"):
                return web.Response(status=500)
        headers = {}
        if state.get("paginate_refunds") and cursor is None:
            url = f"{request.scheme}://{request.host}/admin/api/2024-01/orders/3/refunds.json"
            headers["Link"] = f'<{url}?page_info=next%2Bpage>; rel="next"'
        return web.json_response(
            {
                "refunds": [
                    {
                        "id": 901 if cursor else 900,
                        "order_id": 3,
                        "created_at": "2024-01-16T09:00:00Z",
                        "transactions": [
                            {
                                "kind": "refund",
                                "status": "success",
                                "amount": "4.00",
                                "currency": "USD",
                            }
                        ],
                    }
                ]
            },
            headers=headers,
        )

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
        "SHOP-1",
        "SHOP-2",
        "SHOP-3",
        "SHREF-900",
        "SHOP-4",
        "SHOP-5",
    ]
    assert state["refund_calls"] == ["3"]  # only the partially_refunded order
    assert len(state["order_calls"]) == 4  # 1 throttled + 3 pages
    assert set(state["auth"]) == {"tok"}
    refund = recs[3]
    assert refund["amount"] == "4.00" and refund["payment_reference"] == "3"


def test_feed_includes_every_refund_page(monkeypatch):
    state = {"order_calls": [], "refund_calls": [], "auth": [], "paginate_refunds": True}

    async def go():
        server = TestServer(make_app(state))
        await server.start_server()
        try:
            monkeypatch.setenv("SHOPIFY_STORE_URL", f"http://{server.host}:{server.port}")
            monkeypatch.setenv("SHOPIFY_ACCESS_TOKEN", "tok")
            return await sa.fetch_shopify_feed("S1")
        finally:
            await server.close()

    records = asyncio.run(go())["Shopify"]
    assert [record["source_record_id"] for record in records] == [
        "SHOP-1",
        "SHOP-2",
        "SHOP-3",
        "SHREF-900",
        "SHREF-901",
        "SHOP-4",
        "SHOP-5",
    ]
    assert state["refund_cursors"] == [None, "next+page"]


def test_feed_rejects_partial_refunds_when_later_page_fails(monkeypatch):
    state = {
        "order_calls": [],
        "refund_calls": [],
        "auth": [],
        "paginate_refunds": True,
        "fail_second_refund_page": True,
    }
    monkeypatch.setattr(sa, "retry_delay", lambda *_args: 0)

    async def go():
        server = TestServer(make_app(state))
        await server.start_server()
        try:
            monkeypatch.setenv("SHOPIFY_STORE_URL", f"http://{server.host}:{server.port}")
            monkeypatch.setenv("SHOPIFY_ACCESS_TOKEN", "tok")
            with pytest.raises(sa.ShopifyAPIError, match="Status 500"):
                await sa.fetch_shopify_feed("S1")
        finally:
            await server.close()

    asyncio.run(go())
    assert state["refund_cursors"] == [None, "next+page", "next+page", "next+page"]


def test_adapter_follows_lowercase_repeated_links_with_encoded_cursor():
    calls = []

    async def locations(request):
        cursor = request.query.get("page_info")
        calls.append(cursor)
        if cursor is not None:
            assert cursor == "a+b/c="
            return web.json_response({"locations": [{"id": 2}]})
        url = f"{request.scheme}://{request.host}/admin/api/2024-01/locations.json"
        headers = CIMultiDict(
            [
                ("link", f'<{url}?page_info=old>; rel="previous"'),
                ("link", f'<{url}?page_info=a%2Bb%2Fc%3D>; rel="next"'),
            ]
        )
        return web.json_response({"locations": [{"id": 1}]}, headers=headers)

    async def go():
        app = web.Application()
        app.router.add_get("/admin/api/2024-01/locations.json", locations)
        server = TestServer(app)
        await server.start_server()
        adapter = sa.ShopifyAdapter(
            store_url=f"http://{server.host}:{server.port}", access_token="x"
        )
        try:
            return [location async for location in adapter.fetch_locations()]
        finally:
            await adapter.close()
            await server.close()

    assert asyncio.run(go()) == [{"id": 1}, {"id": 2}]
    assert calls == [None, "a+b/c="]


def test_api_error_propagates():
    async def go():
        async def boom(request):
            return web.Response(status=401, text="bad token")

        app = web.Application()
        app.router.add_get("/admin/api/2024-01/orders.json", boom)
        server = TestServer(app)
        await server.start_server()
        try:
            adapter = sa.ShopifyAdapter(
                store_url=f"http://{server.host}:{server.port}", access_token="x"
            )
            with pytest.raises(sa.ShopifyAPIError, match="401") as error:
                async for _ in adapter.fetch_orders():
                    pass
            assert "bad token" not in str(error.value)
            await adapter.close()
        finally:
            await server.close()

    asyncio.run(go())
