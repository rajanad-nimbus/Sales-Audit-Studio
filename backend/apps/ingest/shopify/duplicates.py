"""Shopify orders stored twice, and orders that look like a customer's double submission.

A repeated order is the same Shopify order (store, currency, order id) stored under a second record
id, typically a file upload alongside the connector. The copy stored first is kept; each later copy
is flagged.

A possible duplicate is a different order id from the same customer, for the same amount and the
same items, placed within a few minutes of an earlier one: a checkout submitted twice or a payment
retry that created a second order. Guest orders carry no customer to compare, and orders Shopify
already voided or fully refunded have been dealt with, so neither is considered.

Only orders in the current reconciliation run are flagged, against every stored order, so the copy
that arrives later is the one that waits for review.
"""

import json
from collections import defaultdict
from datetime import UTC, date, timedelta

from sqlalchemy import select, tuple_

from apps.corrections.effects import effective_amounts
from apps.ingest.audit_rules import chunks, load_lines
from apps.ingest.common import LINEAGE, SHOPIFY_ORDER_KEY_BATCH_SIZE
from apps.ingest.models import SourceRecord
from apps.transactions.models import CanonicalTransaction

REPEATED = "SHOPIFY_DUPLICATE_ORDER"
POSSIBLE = "SHOPIFY_POSSIBLE_DUPLICATE_ORDER"
POSSIBLE_DUPLICATE_WINDOW = timedelta(minutes=10)
SETTLED_ELSEWHERE = {"voided", "refunded"}


async def flag_duplicate_orders(db, transactions, flag) -> set[str]:
    """Flag repeated and possibly duplicated Shopify orders; return the flagged transaction ids."""
    orders = [
        t
        for t in transactions
        if t.source_lineage == LINEAGE["Shopify"]
        and t.transaction_type == "Order"
        and t.reconciliation_status == "Unmatched"
    ]
    if not orders:
        return set()
    amounts = await effective_amounts(db, orders)
    flagged = await flag_repeated_orders(db, orders, amounts, flag)
    flagged |= await flag_possible_duplicates(
        db, [o for o in orders if o.id not in flagged], amounts, flag
    )
    return flagged


async def flag_repeated_orders(db, orders, amounts, flag) -> set[str]:
    by_id = {o.id: o for o in orders}
    keys = sorted(
        {(o.store_id, o.currency, o.payment_reference) for o in orders if o.payment_reference}
    )
    flagged = set()
    for batch in chunks(keys, SHOPIFY_ORDER_KEY_BATCH_SIZE):
        rows = await db.execute(
            select(
                CanonicalTransaction.id,
                CanonicalTransaction.store_id,
                CanonicalTransaction.currency,
                CanonicalTransaction.payment_reference,
            )
            .join(SourceRecord, CanonicalTransaction.source_record_id == SourceRecord.id)
            .where(
                CanonicalTransaction.source_lineage == LINEAGE["Shopify"],
                CanonicalTransaction.transaction_type == "Order",
                tuple_(
                    CanonicalTransaction.store_id,
                    CanonicalTransaction.currency,
                    CanonicalTransaction.payment_reference,
                ).in_(batch),
            )
            .order_by(SourceRecord.received_at, CanonicalTransaction.id)
        )
        seen = set()
        for transaction_id, store, currency, reference in rows.all():
            key = (store, currency, reference)
            if key in seen and transaction_id in by_id:
                flag(REPEATED, abs(amounts[transaction_id]), by_id[transaction_id])
                flagged.add(transaction_id)
            seen.add(key)
    return flagged


def _utc(timestamp):
    return timestamp.replace(tzinfo=UTC) if timestamp.tzinfo is None else timestamp.astimezone(UTC)


async def flag_possible_duplicates(db, orders, amounts, flag) -> set[str]:
    orders = [o for o in orders if o.signed_amount]
    candidates = await nearby_orders(db, orders)
    known = {**candidates, **{o.id: o for o in orders}}
    payloads = await order_payloads(db, known.values())
    lines = await load_lines(db, list(known))

    def signature(order):
        payload = payloads.get(order.id, {})
        customer = payload.get("customer_id")
        if customer in (None, "") or payload.get("financial_status") in SETTLED_ELSEWHERE:
            return None
        items = sorted(
            (line.item_code, str(abs(line.quantity))) for line in lines.get(order.id, [])
        )
        return (order.store_id, order.currency, order.signed_amount, str(customer), tuple(items))

    by_signature = defaultdict(list)
    for order in known.values():
        key = signature(order)
        if key is not None:
            by_signature[key].append(order)

    flagged = set()
    for order in orders:
        key = signature(order)
        if key is None:
            continue
        placed = _utc(order.event_timestamp)
        for other in by_signature[key]:
            earlier = (_utc(other.event_timestamp), other.id) < (placed, order.id)
            if (
                other.payment_reference != order.payment_reference
                and earlier
                and placed - _utc(other.event_timestamp) <= POSSIBLE_DUPLICATE_WINDOW
            ):
                flag(POSSIBLE, abs(amounts[order.id]), order)
                flagged.add(order.id)
                break
    return flagged


async def nearby_orders(db, orders) -> dict[str, CanonicalTransaction]:
    """Stored Shopify orders that share a store, currency and amount with these, a day either side."""
    found = {}
    for batch in chunks(sorted(orders, key=lambda o: (o.business_date, o.id)), 200):
        keys = sorted({(o.store_id, o.currency, o.signed_amount) for o in batch})
        dates = sorted({o.business_date for o in batch})
        rows = await db.execute(
            select(CanonicalTransaction).where(
                CanonicalTransaction.source_lineage == LINEAGE["Shopify"],
                CanonicalTransaction.transaction_type == "Order",
                CanonicalTransaction.business_date.between(
                    _shift(dates[0], -1), _shift(dates[-1], 1)
                ),
                tuple_(
                    CanonicalTransaction.store_id,
                    CanonicalTransaction.currency,
                    CanonicalTransaction.signed_amount,
                ).in_(keys),
            )
        )
        found.update({o.id: o for o in rows.scalars()})
    return found


def _shift(day: str, days: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=days)).isoformat()


async def order_payloads(db, orders) -> dict[str, dict]:
    """The Shopify payload of each order, by transaction id."""
    by_record = {o.source_record_id: o.id for o in orders if o.source_record_id}
    payloads = {}
    for batch in chunks(by_record):
        rows = await db.execute(
            select(SourceRecord.id, SourceRecord.payload).where(SourceRecord.id.in_(batch))
        )
        for record_id, payload in rows.all():
            try:
                payloads[by_record[record_id]] = json.loads(payload)
            except ValueError:
                continue
    return payloads
