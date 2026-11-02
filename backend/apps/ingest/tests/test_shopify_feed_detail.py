"""The live feed asks for order detail and fetches transactions only when tenders are split."""

import asyncio
from decimal import Decimal

from aiohttp import web
from aiohttp.test_utils import TestServer
from apps.ingest.shopify import adapter as sa
from apps.transactions import detail as transaction_detail

TAX = [{"title": "Sales tax", "rate": 0.08, "price": "4.80"}]


def order(order_id, gateways):
    return {
        "id": order_id,
        "created_at": "2026-10-01T10:00:00Z",
        "financial_status": "paid",
        "currency": "USD",
        "total_price": "64.80",
        "payment_gateway_names": gateways,
        "line_items": [
            {"title": "Shirt", "price": "60.00", "quantity": 1, "taxable": True, "sku": "SHIRT"}
        ],
        "tax_lines": TAX,
    }


def run_feed(monkeypatch, orders):
    state = {"order_fields": [], "transaction_calls": []}

    async def list_orders(request):
        state["order_fields"].append(request.query["fields"])
        return web.json_response({"orders": orders})

    async def transactions(request):
        state["transaction_calls"].append(request.match_info["oid"])
        sale = {"kind": "sale", "status": "success", "currency": "USD"}
        return web.json_response(
            {
                "transactions": [
                    {**sale, "id": 1, "gateway": "manual", "amount": "32.40"},
                    {**sale, "id": 2, "gateway": "gift_certificate", "amount": "32.40"},
                ]
            }
        )

    async def go():
        app = web.Application()
        app.router.add_get("/admin/api/2024-01/orders.json", list_orders)
        app.router.add_get("/admin/api/2024-01/orders/{oid}/transactions.json", transactions)
        server = TestServer(app)
        await server.start_server()
        try:
            monkeypatch.setenv("SHOPIFY_STORE_URL", f"http://{server.host}:{server.port}")
            monkeypatch.setenv("SHOPIFY_ACCESS_TOKEN", "tok")
            return await sa.fetch_shopify_feed("S1")
        finally:
            await server.close()

    return asyncio.run(go())["Shopify"], state


def test_feed_requests_the_fields_detail_needs(monkeypatch):
    _, state = run_feed(monkeypatch, [order(1, ["manual"])])
    fields = set(state["order_fields"][0].split(","))
    assert {
        "line_items",
        "shipping_lines",
        "discount_applications",
        "payment_gateway_names",
    } <= fields


def test_single_gateway_order_needs_no_transactions_call(monkeypatch):
    records, state = run_feed(monkeypatch, [order(1, ["manual"])])
    assert state["transaction_calls"] == []
    assert [t["tender_type"] for t in records[0]["tenders"]] == ["Card"]


def test_split_gateway_order_gets_one_tender_per_transaction(monkeypatch):
    records, state = run_feed(monkeypatch, [order(1, ["manual", "gift_certificate"])])
    assert state["transaction_calls"] == ["1"]
    detail = transaction_detail.extract(records[0], 1)
    assert [t["tender_type"] for t in detail["tenders"]] == ["Card", "GiftCard"]
    assert transaction_detail.check(detail, Decimal("64.80"))["status"] == "Balanced"
