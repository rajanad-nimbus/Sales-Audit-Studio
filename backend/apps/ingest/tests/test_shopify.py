"""Unit tests for the Shopify adapter helpers and ingest validation (no network or DB)."""

import asyncio
import base64
import hashlib
import hmac
from decimal import Decimal

import pytest
from apps.ingest.shopify import adapter as sa
from apps.ingest.shopify import ingest as si
from multidict import CIMultiDict


def test_next_page_info_parses_link_header():
    link = (
        '<https://s/admin/api/2024-01/orders.json?limit=250&page_info=prev1>; rel="previous", '
        '<https://s/admin/api/2024-01/orders.json?limit=250&page_info=next2>; rel="next"'
    )
    assert sa._next_page_info(link) == "next2"
    assert sa._next_page_info("") is None


def test_next_page_info_decodes_the_cursor_and_accepts_unquoted_relations():
    link = "<https://s/orders.json?page_info=a%2Bb%2Fc%3D>; rel=next"
    assert sa._next_page_info(link) == "a+b/c="


@pytest.mark.parametrize(
    "link",
    [
        '<https://s/orders.json?limit=250>; rel="next"',
        '<https://s/orders.json?page_info=>; rel="next"',
        '<https://s/orders.json?page_info=a&page_info=b>; rel="next"',
        '<https://s/orders.json?page_info=%ZZ>; rel="next"',
        '<https://s/orders.json?page_info=%FF>; rel="next"',
        '<https://s/orders.json?page_info=a>; rel="next", '
        '<https://s/orders.json?page_info=b>; rel="next"',
        "<https://s/orders.json?page_info=a>; rel=",
    ],
)
def test_next_page_info_rejects_invalid_or_ambiguous_links(link):
    with pytest.raises(sa.ShopifyAPIError, match="invalid pagination link"):
        sa._next_page_info(link)


def test_shopify_pagination_reads_case_insensitive_multiple_link_headers():
    headers = CIMultiDict(
        [
            ("link", '<https://s/orders.json?page_info=old>; rel="previous"'),
            ("LINK", '<https://s/orders.json?page_info=a%2Bb%2Fc%3D>; rel="next"'),
        ]
    )
    params = sa._ShopifyPagination().next_params(headers, {"limit": 250, "fields": "id"})
    assert params == {"limit": 250, "fields": "id", "page_info": "a+b/c="}


def test_request_preserves_repeated_case_insensitive_link_headers(monkeypatch):
    class Content:
        async def iter_chunked(self, _chunk_size):
            yield b'{"locations": []}'

    class Response:
        status = 200
        content_length = None
        content = Content()
        headers = CIMultiDict(
            [
                ("link", '<https://s/locations.json?page_info=old>; rel="previous"'),
                ("link", '<https://s/locations.json?page_info=new>; rel="next"'),
            ]
        )

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class Session:
        def request(self, *_args, **_kwargs):
            return Response()

    async def get_session():
        return Session()

    adapter = sa.ShopifyAdapter(require_credentials=False)
    monkeypatch.setattr(adapter, "_get_session", get_session)
    data, headers = asyncio.run(adapter._request_full("GET", "/locations.json"))
    assert data == {"locations": []}
    assert len(headers.getall("LINK")) == 2
    assert (
        sa._ShopifyPagination().next_params(headers, {"limit": 250, "fields": "id"})["page_info"]
        == "new"
    )


def test_shopify_response_json_is_read_within_byte_limit(monkeypatch):
    class Content:
        async def iter_chunked(self, _chunk_size):
            yield b'{"orders":['
            yield b'{"id":1}]}'

    class Response:
        content_length = None
        content = Content()

    monkeypatch.setattr(sa, "MAX_SHOPIFY_RESPONSE_BYTES", 64)
    assert asyncio.run(sa._read_response_json(Response())) == {"orders": [{"id": 1}]}


def test_shopify_response_rejects_oversized_stream_without_content_length(monkeypatch):
    class Content:
        async def iter_chunked(self, _chunk_size):
            yield b"{" + b" " * 40
            yield b" " * 40 + b"}"

    class Response:
        content_length = None
        content = Content()

    monkeypatch.setattr(sa, "MAX_SHOPIFY_RESPONSE_BYTES", 64)
    with pytest.raises(sa.ShopifyFeedLimitError, match="64-byte limit"):
        asyncio.run(sa._read_response_json(Response()))


def test_shopify_response_rejects_oversized_declared_length(monkeypatch):
    class Content:
        async def iter_chunked(self, _chunk_size):
            raise AssertionError("oversized response should be rejected before reading")
            yield b""

    class Response:
        content_length = 65
        content = Content()

    monkeypatch.setattr(sa, "MAX_SHOPIFY_RESPONSE_BYTES", 64)
    with pytest.raises(sa.ShopifyFeedLimitError, match="64-byte limit"):
        asyncio.run(sa._read_response_json(Response()))


def test_fetch_locations_follows_all_pages(monkeypatch):
    adapter = sa.ShopifyAdapter(require_credentials=False)
    requests = []
    pages = [
        (
            {"locations": [{"id": 1}]},
            {"Link": '<https://shop.test/locations.json?page_info=next>; rel="next"'},
        ),
        ({"locations": [{"id": 2}]}, {}),
    ]

    async def request_full(_method, _endpoint, params=None):
        requests.append(params)
        return pages.pop(0)

    monkeypatch.setattr(adapter, "_request_full", request_full)

    async def consume():
        return [location async for location in adapter.fetch_locations()]

    assert asyncio.run(consume()) == [{"id": 1}, {"id": 2}]
    assert requests[0]["limit"] == 250
    assert requests[1]["page_info"] == "next"


def test_fetch_locations_rejects_repeated_cursor_even_for_empty_pages(monkeypatch):
    adapter = sa.ShopifyAdapter(require_credentials=False)
    calls = 0
    headers = {"Link": '<https://shop.test/locations.json?page_info=repeat>; rel="next"'}

    async def request_full(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {"locations": []}, headers

    monkeypatch.setattr(adapter, "_request_full", request_full)

    async def consume():
        return [location async for location in adapter.fetch_locations()]

    with pytest.raises(sa.ShopifyAPIError, match="repeated pagination cursor"):
        asyncio.run(consume())
    assert calls == 2


def test_shopify_pagination_enforces_page_limit(monkeypatch):
    pagination = sa._ShopifyPagination()
    headers = {"Link": '<https://shop.test/locations.json?page_info=next>; rel="next"'}
    params = {"limit": 250, "fields": "id"}
    monkeypatch.setattr(sa, "MAX_SHOPIFY_PAGES", 2)

    assert pagination.next_params(headers, params)["page_info"] == "next"
    pagination._seen_cursors.clear()
    assert pagination.next_params(headers, params)["page_info"] == "next"
    pagination._seen_cursors.clear()
    with pytest.raises(sa.ShopifyAPIError, match="2-page limit"):
        pagination.next_params(headers, params)


def test_fetch_locations_rejects_malformed_collection_response(monkeypatch):
    adapter = sa.ShopifyAdapter(require_credentials=False)

    async def request_full(*_args, **_kwargs):
        return {"unexpected": []}, {}

    monkeypatch.setattr(adapter, "_request_full", request_full)
    with pytest.raises(sa.ShopifyAPIError, match="invalid locations response"):

        async def consume():
            return [location async for location in adapter.fetch_locations()]

        asyncio.run(consume())


def test_fetch_products_rejects_malformed_collection_response(monkeypatch):
    adapter = sa.ShopifyAdapter(require_credentials=False)

    async def request_full(*_args, **_kwargs):
        return {"products": None}, {}

    monkeypatch.setattr(adapter, "_request_full", request_full)

    async def consume():
        return [product async for product in adapter.fetch_products()]

    with pytest.raises(sa.ShopifyAPIError, match="invalid products response"):
        asyncio.run(consume())


def test_fetch_orders_rejects_malformed_collection_response(monkeypatch):
    adapter = sa.ShopifyAdapter(require_credentials=False)

    async def request_full(*_args, **_kwargs):
        return {"unexpected": []}, {}

    monkeypatch.setattr(adapter, "_request_full", request_full)

    async def consume():
        return [order async for order in adapter.fetch_orders()]

    with pytest.raises(sa.ShopifyAPIError, match="invalid orders response"):
        asyncio.run(consume())


def test_fetch_refunds_rejects_malformed_collection_response(monkeypatch):
    adapter = sa.ShopifyAdapter(require_credentials=False)

    async def request_full(*_args, **_kwargs):
        return {"refunds": None}, {}

    monkeypatch.setattr(adapter, "_request_full", request_full)

    with pytest.raises(sa.ShopifyAPIError, match="invalid refunds response"):
        asyncio.run(adapter.fetch_refunds("order-1"))


def test_fetch_refunds_follows_all_pages(monkeypatch):
    adapter = sa.ShopifyAdapter(require_credentials=False)
    requests = []
    pages = [
        (
            {"refunds": [{"id": 1}]},
            {"link": '<https://shop.test/refunds.json?page_info=a%2Bb>; rel="next"'},
        ),
        ({"refunds": [{"id": 2}]}, {}),
    ]

    async def request_full(method, endpoint, params=None):
        requests.append((method, endpoint, dict(params)))
        return pages.pop(0)

    monkeypatch.setattr(adapter, "_request_full", request_full)

    assert asyncio.run(adapter.fetch_refunds("order-1")) == [{"id": 1}, {"id": 2}]
    assert all(endpoint == "/orders/order-1/refunds.json" for _, endpoint, _ in requests)
    assert requests[0][2]["limit"] == requests[1][2]["limit"] == 250
    assert requests[1][2]["page_info"] == "a+b"


def test_fetch_refunds_rejects_repeated_cursor(monkeypatch):
    adapter = sa.ShopifyAdapter(require_credentials=False)

    async def request_full(*_args, **_kwargs):
        return {"refunds": []}, {"link": '<https://shop.test/refunds.json?page_info=x>; rel="next"'}

    monkeypatch.setattr(adapter, "_request_full", request_full)
    with pytest.raises(sa.ShopifyAPIError, match="repeated pagination cursor"):
        asyncio.run(adapter.fetch_refunds("order-1"))


def test_fetch_refunds_respects_delivery_limit(monkeypatch):
    adapter = sa.ShopifyAdapter(require_credentials=False)
    monkeypatch.setattr(sa, "MAX_DELIVERY_RECORDS", 1)

    async def refunds(_order_id):
        yield {"id": 1}
        yield {"id": 2}

    monkeypatch.setattr(adapter, "iter_refunds", refunds)
    with pytest.raises(sa.ShopifyFeedLimitError, match="1-record"):
        asyncio.run(adapter.fetch_refunds("order-1"))


def test_fetch_order_transactions_rejects_malformed_collection_response(monkeypatch):
    adapter = sa.ShopifyAdapter(require_credentials=False)

    async def request(*_args, **_kwargs):
        return {"transactions": ["not-an-object"]}

    monkeypatch.setattr(adapter, "_request", request)

    with pytest.raises(sa.ShopifyAPIError, match="invalid transactions response"):
        asyncio.run(adapter.fetch_order_transactions("order-1"))


def test_refund_amount_prefers_transactions():
    refund = {
        "transactions": [
            {"kind": "refund", "status": "success", "amount": "12.50"},
            {"kind": "refund", "status": "failure", "amount": "99"},
        ],
        "refund_line_items": [{"quantity": 1, "line_item": {"price": "50"}}],
    }
    assert sa.refund_amount(refund) == Decimal("12.50")


def test_refund_amount_falls_back_to_line_items():
    refund = {"refund_line_items": [{"quantity": 2, "line_item": {"price": "5.00"}}]}
    assert sa.refund_amount(refund) == Decimal("10.00")


def test_normalize_order_handles_null_customer():
    rec = sa.normalize_order(
        {
            "id": 1,
            "created_at": "2024-01-15T10:00:00Z",
            "total_price": "20.00",
            "currency": "USD",
            "customer": None,
        },
        "S1",
    )
    assert rec["payment_reference"] == "1" and rec["customer_id"] is None
    assert si.parse(rec)["amount"] == Decimal("20.00")


def test_normalize_refund_links_to_order():
    rec = sa.normalize_refund(
        {
            "id": 9,
            "created_at": "2024-01-16T10:00:00Z",
            "transactions": [{"kind": "refund", "amount": "5", "currency": "EUR"}],
        },
        1,
        None,
        "S1",
    )
    assert rec["payment_reference"] == rec["order_id"] == "1" and rec["currency"] == "EUR"
    assert si.parse(rec)["amount"] == Decimal("5.00")


def test_parse_quarantine_cases():
    base = {
        "source_record_id": "x",
        "event_time": "2024-01-15T10:00:00Z",
        "amount": "1",
        "store_id": "S",
        "payment_reference": "1",
        "record_type": "Order",
    }
    with pytest.raises(ValueError, match="non-negative"):
        si.parse({**base, "amount": "-1"})
    with pytest.raises(ValueError, match="financial_status"):
        si.parse({**base, "financial_status": "bogus"})
    with pytest.raises(ValueError, match="ISO-8601"):
        si.parse({**base, "event_time": "yesterday"})
    with pytest.raises(ValueError, match="order_id"):
        si.parse({**base, "record_type": "Refund"})


def test_webhook_signature():
    body = b'{"id": 1}'
    good = base64.b64encode(hmac.new(b"secret", body, hashlib.sha256).digest()).decode()
    assert sa.verify_webhook_signature("secret", body, good)
    assert not sa.verify_webhook_signature("secret", body, "bad")
    assert not sa.verify_webhook_signature(None, body, good)


def test_shopify_feed_stops_at_delivery_record_limit(monkeypatch):
    class FakeAdapter:
        closed = False

        async def fetch_orders(self, *_args, **_kwargs):
            for order_id in (1, 2):
                yield {
                    "id": order_id,
                    "created_at": "2024-01-15T10:00:00Z",
                    "total_price": "1.00",
                    "currency": "USD",
                    "financial_status": "paid",
                }

        async def close(self):
            self.closed = True

    adapter = FakeAdapter()
    monkeypatch.setattr(sa, "ShopifyAdapter", lambda: adapter)
    monkeypatch.setattr(sa, "MAX_DELIVERY_RECORDS", 1)

    with pytest.raises(sa.ShopifyFeedLimitError, match="1-record"):
        asyncio.run(sa.fetch_shopify_feed("S1"))
    assert adapter.closed


def test_shopify_feed_enforces_delivery_limit_while_streaming_refunds(monkeypatch):
    class FakeAdapter:
        closed = False
        refunds_read = 0

        async def fetch_orders(self, *_args, **_kwargs):
            yield {
                "id": 1,
                "created_at": "2024-01-15T10:00:00Z",
                "total_price": "5.00",
                "currency": "USD",
                "financial_status": "partially_refunded",
            }

        async def iter_refunds(self, _order_id):
            for refund_id in (1, 2, 3):
                self.refunds_read += 1
                yield {
                    "id": refund_id,
                    "created_at": "2024-01-16T10:00:00Z",
                    "transactions": [
                        {"kind": "refund", "status": "success", "amount": "1.00", "currency": "USD"}
                    ],
                }

        async def close(self):
            self.closed = True

    adapter = FakeAdapter()
    monkeypatch.setattr(sa, "ShopifyAdapter", lambda: adapter)
    monkeypatch.setattr(sa, "MAX_DELIVERY_RECORDS", 2)

    with pytest.raises(sa.ShopifyFeedLimitError, match="2-record"):
        asyncio.run(sa.fetch_shopify_feed("S1"))
    assert adapter.refunds_read == 2
    assert adapter.closed


def test_shopify_feed_stops_at_delivery_byte_limit(monkeypatch):
    class FakeAdapter:
        async def fetch_orders(self, *_args, **_kwargs):
            yield {
                "id": 1,
                "created_at": "2024-01-15T10:00:00Z",
                "total_price": "1.00",
                "currency": "USD",
                "financial_status": "paid",
            }

        async def close(self):
            pass

    monkeypatch.setattr(sa, "ShopifyAdapter", FakeAdapter)
    monkeypatch.setattr(sa, "MAX_DELIVERY_BYTES", 2)

    with pytest.raises(sa.ShopifyFeedLimitError, match="2-byte"):
        asyncio.run(sa.fetch_shopify_feed("S1"))


def test_transport_error_does_not_include_configured_request_url():
    secret_url = "https://shop.example/admin?token=top-secret"

    class FailedRequest:
        async def __aenter__(self):
            raise sa.aiohttp.ClientError(f"connection failed for {secret_url}")

        async def __aexit__(self, *_args):
            pass

    class Session:
        def request(self, *_args, **_kwargs):
            return FailedRequest()

    adapter = sa.ShopifyAdapter(require_credentials=False)
    adapter.max_retries = 1

    async def get_session():
        return Session()

    adapter._get_session = get_session

    with pytest.raises(sa.ShopifyAPIError) as error:
        asyncio.run(adapter._request("GET", "/orders.json"))

    assert "Shopify request failed" in str(error.value)
    assert "top-secret" not in str(error.value)
    assert "shop.example" not in str(error.value)
    assert sa.safe_failure_message(sa.ShopifyAPIError(secret_url)) == "Shopify API request failed"


def test_identity_payload_ignores_mutable_fields():
    a = sa.normalize_order(
        {
            "id": 1,
            "created_at": "2024-01-15T10:00:00Z",
            "total_price": "20.00",
            "financial_status": "paid",
        },
        "S1",
    )
    b = {**a, "financial_status": "refunded", "fulfillment_status": "shipped", "note": "changed"}
    assert si.identity_payload(a) == si.identity_payload(b)
    assert si.identity_payload(a) != si.identity_payload({**a, "amount": "21.00"})
