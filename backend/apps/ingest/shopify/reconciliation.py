"""Match Shopify refunds against their orders during transaction reconciliation."""

import json
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import and_, func, or_, select, tuple_

from apps.corrections.effects import effective_amounts
from apps.ingest.common import (
    LINEAGE,
    MAX_RECONCILIATION_TRANSACTIONS,
    SHOPIFY_ORDER_KEY_BATCH_SIZE,
)
from apps.ingest.controls import accumulate_reconciliation_amounts
from apps.ingest.models import SourceRecord
from apps.transactions.models import CanonicalTransaction


async def reconcile_shopify(db, txns, flag):
    """Match Shopify refunds to their order (by store, currency, order id) and compare totals per order."""
    shopify = [t for t in txns if t.source_lineage == LINEAGE["Shopify"]]
    current_amounts = await effective_amounts(db, shopify)
    refunds = [t for t in shopify if t.transaction_type == "Refund"]
    await close_paid_orders(db, [t for t in shopify if t.transaction_type == "Order"])
    by_order = group_shopify_refunds(refunds)
    group_amounts = current_shopify_refund_amounts(by_order, current_amounts)
    flag_unmatched_shopify_refunds(by_order, group_amounts, flag)
    through_dates = shopify_refund_through_dates(by_order)
    orders_by_key, historical_refund_totals = await load_shopify_order_context(db, through_dates)
    order_amounts = await effective_amounts(db, list(orders_by_key.values()))
    for key, group in by_order.items():
        reconcile_shopify_refund_group(
            key,
            group,
            group_amounts,
            historical_refund_totals,
            orders_by_key,
            order_amounts,
            flag,
        )


def group_shopify_refunds(refunds):
    """Group refunds by the full store, currency, and order identity."""
    by_order = defaultdict(list)
    for refund in refunds:
        by_order[(refund.store_id, refund.currency, refund.payment_reference)].append(refund)
    return by_order


def current_shopify_refund_amounts(by_order, current_amounts):
    """Calculate amounts for the current reconciliation batch."""
    return {
        key: sum((abs(current_amounts[refund.id]) for refund in group), Decimal("0"))
        for key, group in by_order.items()
    }


def flag_unmatched_shopify_refunds(by_order, group_amounts, flag):
    """Flag refunds that lack the order reference required for matching."""
    for key, group in by_order.items():
        if not key[2]:
            flag("SHOPIFY_UNMATCHED_REFUND", group_amounts[key], *group)


def shopify_refund_through_dates(by_order):
    """Return the latest included business date for each referenced order."""
    return {
        key: max(refund.business_date for refund in group)
        for key, group in by_order.items()
        if key[2]
    }


async def load_shopify_order_context(db, through_dates):
    """Fetch earliest matching orders and bounded historical refund totals."""
    orders_by_key = {}
    historical_refund_totals: dict[tuple[str, str, str], Decimal] = defaultdict(Decimal)
    order_keys = list(through_dates)
    for offset in range(0, len(order_keys), SHOPIFY_ORDER_KEY_BATCH_SIZE):
        key_batch = order_keys[offset : offset + SHOPIFY_ORDER_KEY_BATCH_SIZE]
        orders = await load_earliest_shopify_orders(db, key_batch)
        orders_by_key.update(
            {(order.store_id, order.currency, order.payment_reference): order for order in orders}
        )
        await accumulate_shopify_refund_history(
            db, key_batch, through_dates, historical_refund_totals
        )
    return orders_by_key, historical_refund_totals


async def load_earliest_shopify_orders(db, key_batch):
    """Lock the earliest event-time order for every requested identity."""
    key_filter = tuple_(
        CanonicalTransaction.store_id,
        CanonicalTransaction.currency,
        CanonicalTransaction.payment_reference,
    ).in_(key_batch)
    ranked_orders = (
        select(
            CanonicalTransaction.id.label("id"),
            func.row_number()
            .over(
                partition_by=(
                    CanonicalTransaction.store_id,
                    CanonicalTransaction.currency,
                    CanonicalTransaction.payment_reference,
                ),
                order_by=(CanonicalTransaction.event_timestamp, CanonicalTransaction.id),
            )
            .label("order_rank"),
        )
        .where(
            CanonicalTransaction.source_lineage == LINEAGE["Shopify"],
            CanonicalTransaction.transaction_type == "Order",
            key_filter,
        )
        .subquery()
    )
    return (
        (
            await db.execute(
                select(CanonicalTransaction)
                .join(ranked_orders, CanonicalTransaction.id == ranked_orders.c.id)
                .where(ranked_orders.c.order_rank == 1)
                .order_by(CanonicalTransaction.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )


async def accumulate_shopify_refund_history(db, key_batch, through_dates, totals):
    """Accumulate historical refunds through the current reconciliation dates."""
    historical_refund_query = select(CanonicalTransaction).where(
        CanonicalTransaction.source_lineage == LINEAGE["Shopify"],
        CanonicalTransaction.transaction_type == "Refund",
        or_(
            *(
                and_(
                    CanonicalTransaction.store_id == store,
                    CanonicalTransaction.currency == currency,
                    CanonicalTransaction.payment_reference == reference,
                    CanonicalTransaction.business_date
                    <= through_dates[(store, currency, reference)],
                )
                for store, currency, reference in key_batch
            )
        ),
    )
    await accumulate_reconciliation_amounts(
        db,
        historical_refund_query,
        lambda refund: (refund.store_id, refund.currency, refund.payment_reference),
        totals,
        max_groups=MAX_RECONCILIATION_TRANSACTIONS,
        group_description="Shopify refund history groups",
    )


def reconcile_shopify_refund_group(key, group, group_amounts, history, orders, order_amounts, flag):
    """Apply matching, manual-review, partial, and overage decisions for one order."""
    if not key[2]:
        return
    refunded_in_batch = group_amounts[key]
    order = orders.get(key)
    if not order:
        flag("SHOPIFY_UNMATCHED_REFUND", refunded_in_batch, *group)
        return
    if order.reconciliation_status == "Manually Matched":
        flag("SHOPIFY_REFUND_MANUAL_REVIEW", refunded_in_batch, *group)
        return
    # Exclude later refund dates when replaying an earlier reconciliation day.
    refunded_total = abs(history[key])
    order_amount = abs(order_amounts[order.id])
    if refunded_total > order_amount:
        flag("SHOPIFY_OVERAGE_REFUND", refunded_total - order_amount, *group, order)
    elif refunded_total < order_amount:
        flag("SHOPIFY_PARTIAL_REFUND", refunded_total, *group, order)
    else:
        for transaction in (*group, order):
            transaction.reconciliation_status, transaction.disposition = "Matched", "Closed"


async def close_paid_orders(db, orders):
    """Close paid orders that have no refund on file; there is nothing further to reconcile.

    Pending, authorized or voided orders stay open. A refund that arrives later still finds the
    order (matching ignores its status) and reopens it if the totals disagree.
    """
    orders = [o for o in orders if o.reconciliation_status == "Unmatched"]
    if not orders:
        return
    refs = list({o.payment_reference for o in orders if o.payment_reference})
    refunded = set()
    for offset in range(0, len(refs), 500):
        refunded.update(
            (
                await db.execute(
                    select(
                        CanonicalTransaction.store_id,
                        CanonicalTransaction.currency,
                        CanonicalTransaction.payment_reference,
                    )
                    .where(
                        CanonicalTransaction.source_lineage == LINEAGE["Shopify"],
                        CanonicalTransaction.transaction_type == "Refund",
                        CanonicalTransaction.payment_reference.in_(refs[offset : offset + 500]),
                    )
                    .distinct()
                )
            ).all()
        )
    source_ids = list({o.source_record_id for o in orders if o.source_record_id})
    srs = {}
    for offset in range(0, len(source_ids), 500):
        rows = (
            (
                await db.execute(
                    select(SourceRecord).where(
                        SourceRecord.id.in_(source_ids[offset : offset + 500])
                    )
                )
            )
            .scalars()
            .all()
        )
        srs.update({sr.id: sr for sr in rows})
    for o in orders:
        sr = srs.get(o.source_record_id)
        if (
            not o.payment_reference
            or (o.store_id, o.currency, o.payment_reference) in refunded
            or sr is None
        ):
            continue
        if json.loads(sr.payload).get("financial_status") == "paid":
            o.reconciliation_status, o.disposition = "Matched", "Closed"
