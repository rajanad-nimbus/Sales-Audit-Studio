"""Shopify's own end-of-day figures, built from the API during a sync.

A sync declares a day only when its window covers that day from the shop's local midnight to the
next, so a partly fetched day never becomes a figure to audit against. The order count comes from
Shopify's count endpoint, independent of the paged order list, so an order the list skipped still
shows up as a gap. The order total is summed from the raw orders before ingestion parses them, so
an order that was quarantined or edited after it was imported still shows up as a difference.

Refund figures are left out: the connector only sees refunds of orders inside its window, so it
cannot say what Shopify refunded on a day. A day total posted from a Shopify finance export can
carry them.
"""

import hashlib
import json
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

RECORD_PREFIX = "SHOPEOD"


def shop_zone(shop: dict) -> ZoneInfo:
    """The shop's time zone, which is the zone Shopify writes order timestamps in."""
    try:
        return ZoneInfo(shop.get("iana_timezone") or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def covered_days(start: datetime, end: datetime, zone: ZoneInfo) -> list[tuple[date, datetime]]:
    """Local days lying wholly inside [start, end], each with its local midnight."""
    day = start.astimezone(zone).date()
    if datetime.combine(day, time.min, tzinfo=zone) < start:
        day += timedelta(days=1)
    days = []
    while datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone) <= end:
        days.append((day, datetime.combine(day, time.min, tzinfo=zone)))
        day += timedelta(days=1)
    return days


def business_time(record: dict):
    """When an order or refund counts as happening: processed_at, else created_at.

    Shopify dates its own reports by processed_at, and it is the only date an imported or backdated
    order carries; created_at is always the moment the record reached Shopify.
    """
    return record.get("processed_at") or record.get("created_at")


def order_business_date(timestamp) -> date | None:
    """The business date ingestion gives the order: the date in the offset Shopify wrote."""
    try:
        return datetime.fromisoformat(str(timestamp).replace("Z", "+00:00")).date()
    except ValueError:
        return None


class OrderTally:
    """Count and total of the listed orders per business date and currency."""

    def __init__(self) -> None:
        self._days: dict[date, dict[str, list]] = defaultdict(
            lambda: defaultdict(lambda: [0, Decimal("0")])
        )

    def add(self, order: dict) -> None:
        day = order_business_date(business_time(order))
        if day is None:
            return
        entry = self._days[day][order.get("currency", "USD")]
        entry[0] += 1
        try:
            entry[1] += Decimal(str(order.get("total_price", "0")))
        except InvalidOperation:
            pass  # ingestion quarantines this order; the day total then shows the gap

    def on(self, day: date) -> dict[str, list]:
        return self._days.get(day, {})


def day_total_record(store_id: str, day: date, currency: str, count: int, total: Decimal) -> dict:
    """One day total. Its id carries a digest of the figures, so a revised figure is a new record."""
    figures = [store_id, day.isoformat(), currency, count, str(total)]
    digest = hashlib.sha256(json.dumps(figures).encode()).hexdigest()[:12]
    return {
        "source_record_id": f"{RECORD_PREFIX}-{store_id}-{day.isoformat()}-{currency}-{digest}",
        "record_type": "DayTotal",
        "store_id": store_id,
        "business_date": day.isoformat(),
        "currency": currency,
        "order_count": count,
        "order_total": str(total),
    }


def day_total_records(
    store_id: str, shop_currency: str, counts: dict[date, int], tally: OrderTally
) -> list[dict]:
    """Day totals for each counted day. A day with no orders still declares zero, in shop currency.

    Shopify counts orders across currencies, so the count endpoint is used only when the day has a
    single currency; a multi-currency day falls back to the listed count for each currency.
    """
    records = []
    for day in sorted(counts):
        listed = tally.on(day) or {shop_currency: [0, Decimal("0")]}
        single_currency = len(listed) == 1
        for currency, (listed_count, total) in sorted(listed.items()):
            count = counts[day] if single_currency else listed_count
            records.append(day_total_record(store_id, day, currency, count, total))
    return records
