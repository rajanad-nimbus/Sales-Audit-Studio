"""Shopify REST API adapter for fetching orders and refunds."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import math
import os
import re
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from email.utils import parsedate_to_datetime

import aiohttp

from apps.ingest.limits import MAX_DELIVERY_BYTES, MAX_DELIVERY_RECORDS


class ShopifyAPIError(Exception):
    """Shopify API error."""

    pass


class ShopifyFeedLimitError(ShopifyAPIError):
    """The remote feed exceeds the ingestion limits and cannot be collected safely."""


def safe_failure_message(error: Exception) -> str:
    """Describe an integration failure without exposing request URLs or remote details."""
    if isinstance(error, asyncio.TimeoutError):
        return "Shopify request timed out"
    if isinstance(error, aiohttp.ClientError):
        return "Shopify request failed"
    if isinstance(error, ShopifyAPIError):
        message = str(error)
        safe_patterns = (
            r"Shopify API response exceeds the [\d,]+-byte limit",
            r"Shopify feed exceeds the [\d,]+-record delivery limit",
            r"Shopify feed exceeds the [\d,]+-byte delivery limit",
            r"Shopify pagination exceeded the [\d,]+-page limit",
            r"Shopify returned a repeated pagination cursor",
            r"Shopify returned an invalid JSON response",
            r"Shopify returned an invalid (shop|locations|products|orders|refunds|transactions) response",
            r"Status \d+ after \d+ attempts",
            r"Shopify API returned HTTP \d+",
            r"Request failed after \d+ attempts: Shopify request (timed out|failed)",
        )
        if any(re.fullmatch(pattern, message) for pattern in safe_patterns):
            return message
        return "Shopify API request failed"
    return f"Shopify operation failed ({type(error).__name__})"


MAX_RETRY_DELAY_SECONDS = 30.0
MAX_SHOPIFY_PAGES = 1_000
MAX_SHOPIFY_RESPONSE_BYTES = MAX_DELIVERY_BYTES
SHOPIFY_RESPONSE_CHUNK_BYTES = 64 * 1024


async def _read_response_json(response: aiohttp.ClientResponse) -> dict:
    """Read a Shopify response without buffering an unbounded body in memory."""
    content_length = response.content_length
    if content_length is not None and content_length > MAX_SHOPIFY_RESPONSE_BYTES:
        raise ShopifyFeedLimitError(
            f"Shopify API response exceeds the {MAX_SHOPIFY_RESPONSE_BYTES:,}-byte limit"
        )

    body = bytearray()
    async for chunk in response.content.iter_chunked(SHOPIFY_RESPONSE_CHUNK_BYTES):
        if len(body) + len(chunk) > MAX_SHOPIFY_RESPONSE_BYTES:
            raise ShopifyFeedLimitError(
                f"Shopify API response exceeds the {MAX_SHOPIFY_RESPONSE_BYTES:,}-byte limit"
            )
        body.extend(chunk)

    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ShopifyAPIError("Shopify returned an invalid JSON response") from exc
    if not isinstance(payload, dict):
        raise ShopifyAPIError("Shopify returned an invalid JSON response")
    return payload


class _ShopifyPagination:
    """Track cursor progress and bound a collection fetch to a finite number of pages."""

    def __init__(self) -> None:
        self._seen_cursors: set[str] = set()
        self._pages_fetched = 0

    def next_params(self, headers: dict, params: dict) -> dict | None:
        self._pages_fetched += 1
        if self._pages_fetched > MAX_SHOPIFY_PAGES:
            raise ShopifyAPIError(f"Shopify pagination exceeded the {MAX_SHOPIFY_PAGES}-page limit")

        page_info = _next_page_info(headers.get("Link", ""))
        if not page_info:
            return None
        if page_info in self._seen_cursors:
            raise ShopifyAPIError("Shopify returned a repeated pagination cursor")
        self._seen_cursors.add(page_info)
        return {"limit": params["limit"], "page_info": page_info, "fields": params["fields"]}


def retry_delay(retry_after: str | None, attempt: int) -> float:
    """Honor a valid Retry-After without allowing a remote server to stall a job indefinitely."""
    if retry_after:
        try:
            seconds = float(retry_after)
            if math.isfinite(seconds) and seconds > 0:
                return min(seconds, MAX_RETRY_DELAY_SECONDS)
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(retry_after)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=UTC)
                seconds = (retry_at - datetime.now(UTC)).total_seconds()
                if seconds > 0:
                    return min(seconds, MAX_RETRY_DELAY_SECONDS)
            except (TypeError, ValueError, OverflowError):
                pass
    return min(2**attempt, MAX_RETRY_DELAY_SECONDS)


class ShopifyAdapter:
    """Async Shopify REST API client."""

    def __init__(
        self,
        store_url: str = None,
        api_key: str = None,
        api_password: str = None,
        api_version: str = None,
        access_token: str = None,
        require_credentials: bool = True,
    ):
        self.store_url = store_url or os.getenv("SHOPIFY_STORE_URL")
        self.api_key = api_key or os.getenv("SHOPIFY_API_KEY")
        self.api_password = api_password or os.getenv("SHOPIFY_API_PASSWORD")
        # Custom apps authenticate with an Admin API access token (X-Shopify-Access-Token);
        # key/password basic auth is only for legacy private apps.
        self.access_token = access_token or os.getenv("SHOPIFY_ACCESS_TOKEN")
        self.api_version = api_version or os.getenv("SHOPIFY_API_VERSION", "2024-01")
        self.webhook_secret = os.getenv("SHOPIFY_WEBHOOK_SECRET")

        if require_credentials and not (
            self.store_url and (self.access_token or (self.api_key and self.api_password))
        ):
            raise ValueError("Shopify credentials not configured")

        self.base_url = f"{(self.store_url or '').rstrip('/')}/admin/api/{self.api_version}"
        self.session = None
        self.max_retries = 3

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self.session is None or self.session.closed:
            if self.access_token:
                self.session = aiohttp.ClientSession(
                    headers={"X-Shopify-Access-Token": self.access_token}
                )
            else:
                self.session = aiohttp.ClientSession(
                    auth=aiohttp.BasicAuth(self.api_key, self.api_password)
                )
        return self.session

    async def close(self):
        """Close session."""
        if self.session:
            await self.session.close()

    async def _request_full(
        self, method: str, endpoint: str, params: dict = None
    ) -> tuple[dict, dict]:
        """Make an authenticated request with retries. Returns (json body, response headers)."""
        url = f"{self.base_url}{endpoint}"
        session = await self._get_session()
        last_error = None

        for attempt in range(self.max_retries):
            last = attempt == self.max_retries - 1
            try:
                async with session.request(
                    method, url, params=params, timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status == 429 or resp.status >= 500:
                        last_error = f"Status {resp.status}"
                        if not last:
                            await asyncio.sleep(
                                retry_delay(resp.headers.get("Retry-After"), attempt)
                            )
                            continue
                        raise ShopifyAPIError(f"{last_error} after {self.max_retries} attempts")

                    if resp.status >= 400:
                        raise ShopifyAPIError(f"Shopify API returned HTTP {resp.status}")

                    return await _read_response_json(resp), dict(resp.headers)
            except (TimeoutError, aiohttp.ClientError) as e:
                last_error = safe_failure_message(e)
                if not last:
                    await asyncio.sleep(retry_delay(None, attempt))
                    continue
                raise ShopifyAPIError(
                    f"Request failed after {self.max_retries} attempts: {last_error}"
                ) from e

        raise ShopifyAPIError(f"Request failed after {self.max_retries} attempts: {last_error}")

    async def _request(self, method: str, endpoint: str, params: dict = None) -> dict:
        data, _ = await self._request_full(method, endpoint, params)
        return data

    async def fetch_orders(
        self,
        created_at_min: datetime = None,
        created_at_max: datetime = None,
        limit: int = None,
        status: str = "any",
    ) -> AsyncGenerator[dict, None]:
        """Fetch orders, following Shopify's Link-header cursor pagination."""
        lookback = int(os.getenv("SHOPIFY_SYNC_LOOKBACK_DAYS", "3"))
        limit = limit or int(os.getenv("SHOPIFY_FETCH_LIMIT", "250"))
        if created_at_min is None:
            created_at_min = datetime.now(UTC) - timedelta(days=lookback)
        if created_at_max is None:
            created_at_max = datetime.now(UTC)

        params = {
            "limit": min(limit, 250),
            "status": status,
            "created_at_min": created_at_min.isoformat(),
            "created_at_max": created_at_max.isoformat(),
            "fields": "id,order_number,created_at,updated_at,financial_status,fulfillment_status,"
            "total_price,currency,customer,note,tax_lines",
        }
        pagination = _ShopifyPagination()

        while True:
            data, headers = await self._request_full("GET", "/orders.json", params=params)
            for order in self._collection_page(data, "orders", "orders"):
                yield order

            next_params = pagination.next_params(headers, params)
            if next_params is None:
                break
            # With page_info, Shopify rejects every filter except limit/fields.
            params = next_params

    async def fetch_shop(self) -> dict:
        """Fetch the shop record (its name labels the store the orders belong to)."""
        data = await self._request(
            "GET", "/shop.json", params={"fields": "id,name,currency,iana_timezone"}
        )
        shop = data.get("shop") if isinstance(data, dict) else None
        if not isinstance(shop, dict) or not shop.get("id"):
            raise ShopifyAPIError("Shopify returned an invalid shop response")
        return shop

    @staticmethod
    def _collection_page(data: dict, key: str, endpoint: str) -> list[dict]:
        """Reject malformed collection responses instead of treating them as an empty catalog."""
        records = data.get(key) if isinstance(data, dict) else None
        if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
            raise ShopifyAPIError(f"Shopify returned an invalid {endpoint} response")
        return records

    async def fetch_locations(self) -> AsyncGenerator[dict, None]:
        """Stream every shop location while following Shopify's Link-header pagination."""
        params = {"limit": 250, "fields": "id,name,active,city,province,country_code"}
        pagination = _ShopifyPagination()
        while True:
            data, headers = await self._request_full("GET", "/locations.json", params=params)
            for location in self._collection_page(data, "locations", "locations"):
                yield location
            next_params = pagination.next_params(headers, params)
            if next_params is None:
                break
            params = next_params

    async def fetch_products(self) -> AsyncGenerator[dict, None]:
        """Fetch the product catalog with variants, following Link-header pagination."""
        params = {"limit": 250, "fields": "id,title,status,vendor,product_type,variants"}
        pagination = _ShopifyPagination()
        while True:
            data, headers = await self._request_full("GET", "/products.json", params=params)
            for product in self._collection_page(data, "products", "products"):
                yield product
            next_params = pagination.next_params(headers, params)
            if next_params is None:
                break
            params = next_params

    async def fetch_refunds(self, order_id: str) -> list[dict]:
        """Fetch refunds for a specific order."""
        data = await self._request(
            "GET",
            f"/orders/{order_id}/refunds.json",
            params={"fields": "id,order_id,created_at,note,refund_line_items,transactions"},
        )
        return self._collection_page(data, "refunds", "refunds")

    async def fetch_order_transactions(self, order_id: str) -> list[dict]:
        """Fetch payment transactions for an order (captures, authorizations)."""
        data = await self._request(
            "GET",
            f"/orders/{order_id}/transactions.json",
            params={"fields": "id,type,kind,amount,currency,created_at,message"},
        )
        return self._collection_page(data, "transactions", "transactions")

    def verify_webhook_signature(self, body: bytes, signature: str) -> bool:
        """Verify Shopify webhook HMAC-SHA256 signature."""
        return verify_webhook_signature(self.webhook_secret, body, signature)


def verify_webhook_signature(secret: str | None, body: bytes, signature: str) -> bool:
    """Verify an X-Shopify-Hmac-SHA256 header against the raw request body."""
    if not secret or not signature:
        return False
    computed = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(computed).decode(), signature)


def _next_page_info(link_header: str) -> str | None:
    """Extract page_info of the rel="next" link from a Link header."""
    for part in link_header.split(","):
        if 'rel="next"' in part:
            m = re.search(r"[?&]page_info=([^&>]+)", part)
            if m:
                return m.group(1)
    return None


async def fetch_shopify_feed(
    store_id: str,
    created_at_min: datetime = None,
    created_at_max: datetime = None,
) -> dict[str, list[dict]]:
    """Fetch Shopify orders and refunds. Any API failure propagates so no partial feed is ingested."""
    adapter = ShopifyAdapter()
    records = []
    payload_size = 2  # opening and closing brackets of the JSON record list

    def append_record(record: dict) -> None:
        nonlocal payload_size
        if len(records) >= MAX_DELIVERY_RECORDS:
            raise ShopifyFeedLimitError(
                f"Shopify feed exceeds the {MAX_DELIVERY_RECORDS:,}-record delivery limit"
            )
        encoded_size = len(
            json.dumps(record, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        )
        next_size = payload_size + encoded_size + bool(records)
        if next_size > MAX_DELIVERY_BYTES:
            raise ShopifyFeedLimitError(
                f"Shopify feed exceeds the {MAX_DELIVERY_BYTES:,}-byte delivery limit"
            )
        records.append(record)
        payload_size = next_size

    try:
        async for order in adapter.fetch_orders(created_at_min, created_at_max):
            append_record(normalize_order(order, store_id))

            if order.get("financial_status") in ("refunded", "partially_refunded"):
                for refund in await adapter.fetch_refunds(order["id"]):
                    append_record(
                        normalize_refund(refund, order["id"], order.get("currency"), store_id)
                    )

        return {"Shopify": records}
    finally:
        await adapter.close()


def _tax(code: str, name: str, rate, amount) -> dict:
    # Preserve source precision. The ingestion validator must quarantine values the
    # database cannot represent instead of silently changing Shopify's figures.
    return {
        "tax_code": code[:30],
        "tax_name": name[:100],
        "rate": str(Decimal(str(rate or 0))),
        "tax_amount": str(abs(Decimal(str(amount or 0)))),
    }


def order_taxes(order: dict) -> list[dict]:
    """Tax lines Shopify reports on an order, one entry per tax."""
    return [
        _tax(
            str(t.get("title") or "TAX"),
            str(t.get("title") or "TAX"),
            t.get("rate"),
            t.get("price"),
        )
        for t in order.get("tax_lines") or []
        if Decimal(str(t.get("price") or 0)) != 0
    ]


def refund_taxes(refund: dict) -> list[dict]:
    """Tax returned with a refund: the tax on its refunded line items, as one entry."""
    total = sum(
        (Decimal(str(li.get("total_tax") or 0)) for li in refund.get("refund_line_items") or []),
        Decimal("0"),
    )
    return [_tax("TAX", "Refunded tax", 0, total)] if total else []


def normalize_order(order: dict, store_id: str) -> dict:
    """Convert a Shopify order (REST or webhook payload) to ingest format."""
    return {
        "source_record_id": f"SHOP-{order['id']}",
        "record_type": "Order",
        "store_id": store_id,
        "event_time": order["created_at"],
        "amount": str(order.get("total_price", "0")),
        "currency": order.get("currency", "USD"),
        "payment_reference": str(order["id"]),
        "financial_status": order.get("financial_status") or "pending",
        "fulfillment_status": order.get("fulfillment_status"),
        "order_number": order.get("order_number"),
        "customer_id": (order.get("customer") or {}).get("id"),
        "note": order.get("note"),
        "taxes": order_taxes(order),
    }


def refund_amount(refund: dict) -> Decimal:
    """Money actually refunded: successful refund transactions, else line-item subtotals."""
    txns = [
        t
        for t in refund.get("transactions") or []
        if t.get("kind") == "refund" and t.get("status", "success") == "success"
    ]
    if txns:
        return sum((Decimal(str(t.get("amount", "0"))) for t in txns), Decimal("0"))
    return sum(
        (
            Decimal(
                str(
                    li.get("subtotal")
                    if li.get("subtotal") is not None
                    else Decimal(str(li.get("quantity", 0)))
                    * Decimal(str((li.get("line_item") or {}).get("price", "0")))
                )
            )
            for li in refund.get("refund_line_items") or []
        ),
        Decimal("0"),
    )


def normalize_refund(refund: dict, order_id, currency: str | None, store_id: str) -> dict:
    """Convert a Shopify refund (REST or webhook payload) to ingest format."""
    txn_currency = next(
        (t.get("currency") for t in refund.get("transactions") or [] if t.get("currency")), None
    )
    return {
        "source_record_id": f"SHREF-{refund['id']}",
        "record_type": "Refund",
        "store_id": store_id,
        "event_time": refund["created_at"],
        "amount": str(refund_amount(refund)),
        "currency": currency or txn_currency or "USD",
        "payment_reference": str(order_id),
        "refund_id": str(refund["id"]),
        "order_id": str(order_id),
        "note": refund.get("note"),
        "taxes": refund_taxes(refund),
    }
