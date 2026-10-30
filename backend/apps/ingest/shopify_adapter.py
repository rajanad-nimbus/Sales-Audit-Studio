"""Shopify REST API adapter for fetching orders and refunds."""
from __future__ import annotations
import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import AsyncGenerator

import aiohttp


class ShopifyAPIError(Exception):
    """Shopify API error."""
    pass


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
        self.retry_backoff = 1  # seconds

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self.session is None or self.session.closed:
            if self.access_token:
                self.session = aiohttp.ClientSession(headers={"X-Shopify-Access-Token": self.access_token})
            else:
                self.session = aiohttp.ClientSession(auth=aiohttp.BasicAuth(self.api_key, self.api_password))
        return self.session

    async def close(self):
        """Close session."""
        if self.session:
            await self.session.close()

    async def _request_full(self, method: str, endpoint: str, params: dict = None) -> tuple[dict, dict]:
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
                            try:
                                wait = float(resp.headers.get("Retry-After", 0))
                            except ValueError:
                                wait = 0
                            await asyncio.sleep(wait or self.retry_backoff * (2 ** attempt))
                            continue
                        raise ShopifyAPIError(f"{last_error} after {self.max_retries} attempts")

                    if resp.status >= 400:
                        text = await resp.text()
                        raise ShopifyAPIError(f"Status {resp.status}: {text}")

                    return await resp.json(), dict(resp.headers)
            except (asyncio.TimeoutError, aiohttp.ClientError) as e:
                last_error = str(e) or type(e).__name__
                if not last:
                    await asyncio.sleep(self.retry_backoff * (2 ** attempt))
                    continue
                raise ShopifyAPIError(f"Request failed after {self.max_retries} attempts: {last_error}") from e

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
            created_at_min = datetime.now(timezone.utc) - timedelta(days=lookback)
        if created_at_max is None:
            created_at_max = datetime.now(timezone.utc)

        params = {
            "limit": min(limit, 250),
            "status": status,
            "created_at_min": created_at_min.isoformat(),
            "created_at_max": created_at_max.isoformat(),
            "fields": "id,order_number,created_at,updated_at,financial_status,fulfillment_status,"
                      "total_price,currency,customer,note",
        }

        while True:
            data, headers = await self._request_full("GET", "/orders.json", params=params)
            for order in data.get("orders", []):
                yield order

            page_info = _next_page_info(headers.get("Link", ""))
            if not page_info:
                break
            # With page_info, Shopify rejects every filter except limit/fields.
            params = {"limit": params["limit"], "page_info": page_info, "fields": params["fields"]}

    async def fetch_refunds(self, order_id: str) -> list[dict]:
        """Fetch refunds for a specific order."""
        data = await self._request(
            "GET",
            f"/orders/{order_id}/refunds.json",
            params={"fields": "id,order_id,created_at,note,refund_line_items,transactions"},
        )
        return data.get("refunds", [])

    async def fetch_order_transactions(self, order_id: str) -> list[dict]:
        """Fetch payment transactions for an order (captures, authorizations)."""
        data = await self._request(
            "GET",
            f"/orders/{order_id}/transactions.json",
            params={"fields": "id,type,kind,amount,currency,created_at,message"},
        )
        return data.get("transactions", [])

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

    try:
        async for order in adapter.fetch_orders(created_at_min, created_at_max):
            records.append(normalize_order(order, store_id))

            if order.get("financial_status") in ("refunded", "partially_refunded"):
                for refund in await adapter.fetch_refunds(order["id"]):
                    records.append(normalize_refund(refund, order["id"], order.get("currency"), store_id))

        return {"Shopify": records}
    finally:
        await adapter.close()


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
    }


def refund_amount(refund: dict) -> Decimal:
    """Money actually refunded: successful refund transactions, else line-item subtotals."""
    txns = [t for t in refund.get("transactions") or []
            if t.get("kind") == "refund" and t.get("status", "success") == "success"]
    if txns:
        return sum((Decimal(str(t.get("amount", "0"))) for t in txns), Decimal("0"))
    return sum(
        (Decimal(str(li.get("subtotal") if li.get("subtotal") is not None
                      else Decimal(str(li.get("quantity", 0))) * Decimal(str((li.get("line_item") or {}).get("price", "0")))))
         for li in refund.get("refund_line_items") or []),
        Decimal("0"),
    )


def normalize_refund(refund: dict, order_id, currency: str | None, store_id: str) -> dict:
    """Convert a Shopify refund (REST or webhook payload) to ingest format."""
    txn_currency = next((t.get("currency") for t in refund.get("transactions") or [] if t.get("currency")), None)
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
    }
