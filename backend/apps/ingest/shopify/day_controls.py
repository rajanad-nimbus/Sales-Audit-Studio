"""Check each Shopify store day against the day total Shopify reported for it.

Shopify's figure is the control, the way a POS control manifest is for a store: fewer orders or
refunds stored than Shopify reports is an incomplete feed; any other difference in count or amount
is a store day total mismatch. Amounts are compared after approved corrections, like every other
control, and the active policy's amount tolerance applies in its currency.

A day total waits as an archived Shopify record until reconciliation checks it, then becomes
Processed. When Shopify revised a day before it was checked, only the latest figure is checked and
the earlier ones become Superseded.
"""

import json
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select, tuple_

from apps.ingest.common import COMPOSITE_SCOPE_BATCH_SIZE, LINEAGE, MAX_SETTLEMENT_GROUPS
from apps.ingest.controls import accumulate_reconciliation_amounts
from apps.ingest.manifests import iter_manifests
from apps.transactions.models import CanonicalTransaction

INCOMPLETE = "SHOPIFY_INCOMPLETE_FEED"
MISMATCH = "SHOPIFY_EOD_TOTAL_MISMATCH"


@dataclass(frozen=True, slots=True)
class DayTotal:
    order_count: int
    order_total: Decimal
    refund_count: int | None = None
    refund_total: Decimal | None = None

    @classmethod
    def from_payload(cls, payload: dict) -> "DayTotal":
        refunds = payload.get("refund_count") not in (None, "")
        return cls(
            int(payload["order_count"]),
            Decimal(str(payload["order_total"])),
            int(payload["refund_count"]) if refunds else None,
            Decimal(str(payload["refund_total"])) if refunds else None,
        )


@dataclass(slots=True)
class StoredDay:
    order_count: int = 0
    order_total: Decimal = Decimal("0")
    refund_count: int = 0
    refund_total: Decimal = Decimal("0")


def compare(
    declared: DayTotal, stored: StoredDay, tolerance: Decimal
) -> tuple[str, Decimal] | None:
    """The finding for one store day, with the amount it is off by, or None when it agrees."""
    short = stored.order_count < declared.order_count
    counts_differ = stored.order_count != declared.order_count
    gap = abs(declared.order_total - stored.order_total)
    amounts_differ = gap > tolerance
    if declared.refund_count is not None:
        refund_gap = abs(declared.refund_total - stored.refund_total)
        short |= stored.refund_count < declared.refund_count
        counts_differ |= stored.refund_count != declared.refund_count
        amounts_differ |= refund_gap > tolerance
        gap += refund_gap
    if short:
        return INCOMPLETE, gap
    if counts_differ or amounts_differ:
        return MISMATCH, gap
    return None


async def run_shopify_day_controls(
    db, business_date, add, policy=None, *, store_ids: set[str] | None = None
):
    """Raise a finding for every pending Shopify day total its store day does not agree with."""
    latest = await pending_day_totals(db, business_date, store_ids)
    scopes = sorted(latest)
    for offset in range(0, len(scopes), COMPOSITE_SCOPE_BATCH_SIZE):
        batch = scopes[offset : offset + COMPOSITE_SCOPE_BATCH_SIZE]
        stored = await stored_days(db, batch)
        for scope in batch:
            record, declared = latest[scope]
            store, currency, day = scope
            tolerance = (
                policy.amount_tolerance
                if policy and currency == policy.amount_tolerance_currency
                else Decimal("0")
            )
            finding = compare(declared, stored.get(scope, StoredDay()), tolerance)
            if finding:
                add(finding[0], store, day, finding[1], currency)
            record.status = "Processed"


async def pending_day_totals(db, business_date, store_ids):
    """The latest unchecked day total per (store, currency, business date); older ones are superseded."""
    pending = defaultdict(list)
    async for record in iter_manifests(db, business_date, ("Archived",), "Shopify"):
        payload = json.loads(record.payload)
        if payload.get("record_type") != "DayTotal":
            continue
        if store_ids is not None and payload["store_id"] not in store_ids:
            continue
        scope = (payload["store_id"], payload.get("currency", "USD"), payload["business_date"])
        pending[scope].append((record, payload))
    latest = {}
    for scope, records in pending.items():
        records.sort(key=lambda item: (item[0].received_at, item[0].id))
        for record, _payload in records[:-1]:
            record.status = "Superseded"
        record, payload = records[-1]
        latest[scope] = (record, DayTotal.from_payload(payload))
    return latest


async def stored_days(db, scopes) -> dict[tuple[str, str, str], StoredDay]:
    """Count and corrected total of the stored Shopify orders and refunds for each store day."""
    scope_filter = tuple_(
        CanonicalTransaction.store_id,
        CanonicalTransaction.currency,
        CanonicalTransaction.business_date,
    ).in_(scopes)
    shopify = (CanonicalTransaction.source_lineage == LINEAGE["Shopify"], scope_filter)
    days: dict[tuple[str, str, str], StoredDay] = defaultdict(StoredDay)
    rows = await db.execute(
        select(
            CanonicalTransaction.store_id,
            CanonicalTransaction.currency,
            CanonicalTransaction.business_date,
            CanonicalTransaction.transaction_type,
            func.count(),
        )
        .where(*shopify)
        .group_by(
            CanonicalTransaction.store_id,
            CanonicalTransaction.currency,
            CanonicalTransaction.business_date,
            CanonicalTransaction.transaction_type,
        )
    )
    for store, currency, day, kind, count in rows.all():
        if kind == "Order":
            days[(store, currency, day)].order_count = count
        elif kind == "Refund":
            days[(store, currency, day)].refund_count = count

    totals: dict[tuple[str, str, str, str], Decimal] = defaultdict(Decimal)
    await accumulate_reconciliation_amounts(
        db,
        select(CanonicalTransaction).where(*shopify),
        lambda t: (t.store_id, t.currency, t.business_date, t.transaction_type),
        totals,
        max_groups=MAX_SETTLEMENT_GROUPS,
        group_description="Shopify day total groups",
    )
    for (store, currency, day, kind), amount in totals.items():
        if kind == "Order":
            days[(store, currency, day)].order_total = amount
        elif kind == "Refund":
            days[(store, currency, day)].refund_total = -amount
    return days
