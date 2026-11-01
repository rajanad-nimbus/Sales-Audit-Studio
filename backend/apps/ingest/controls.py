"""Completeness, balancing, and settlement controls for reconciliation."""

import json
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import func, select, tuple_, update

from apps.corrections.effects import effective_amounts
from apps.ingest.common import (
    COMPOSITE_SCOPE_BATCH_SIZE,
    LINEAGE,
    MAX_SETTLEMENT_GROUPS,
    SETTLEMENT_QUERY_PAGE_SIZE,
    ReconciliationCapacityError,
)
from apps.ingest.manifests import iter_manifests
from apps.transactions.models import CanonicalTransaction


async def run_controls(db, business_date, add, policy=None, *, store_ids: set[str] | None = None):
    """Completeness and balancing: compare POS control manifests with ingested POS transactions."""
    batch = []
    async for m in iter_manifests(db, business_date, ("Archived",)):
        d = json.loads(m.payload)
        store, bd = d["store_id"], str(d["business_date"])[:10]
        if store_ids is not None and store not in store_ids:
            continue
        if business_date and bd != business_date:
            continue
        batch.append(
            (
                m,
                store,
                bd,
                d.get("currency", "USD"),
                int(d["declared_count"]),
                Decimal(str(d["declared_total"])),
            )
        )
        if len(batch) == COMPOSITE_SCOPE_BATCH_SIZE:
            await reconcile_control_manifest_batch(db, batch, add, policy)
            batch.clear()
    if batch:
        await reconcile_control_manifest_batch(db, batch, add, policy)


async def reconcile_control_manifest_batch(db, manifests, add, policy=None):
    """Compare a bounded group of control manifests using grouped counts and paged corrected totals."""
    scopes = sorted(
        {
            (store, currency, business_date)
            for _manifest, store, business_date, currency, _count, _total in manifests
        }
    )
    transaction_counts = {}
    transaction_totals: dict[tuple[str, str, str], Decimal] = defaultdict(Decimal)

    for offset in range(0, len(scopes), COMPOSITE_SCOPE_BATCH_SIZE):
        scope_batch = scopes[offset : offset + COMPOSITE_SCOPE_BATCH_SIZE]
        scope_filter = tuple_(
            CanonicalTransaction.store_id,
            CanonicalTransaction.currency,
            CanonicalTransaction.business_date,
        ).in_(scope_batch)
        count_rows = (
            await db.execute(
                select(
                    CanonicalTransaction.store_id,
                    CanonicalTransaction.currency,
                    CanonicalTransaction.business_date,
                    func.count(),
                )
                .where(
                    CanonicalTransaction.source_lineage == LINEAGE["POS"],
                    scope_filter,
                )
                .group_by(
                    CanonicalTransaction.store_id,
                    CanonicalTransaction.currency,
                    CanonicalTransaction.business_date,
                )
            )
        ).all()
        transaction_counts.update(
            {(store, currency, day): count for store, currency, day, count in count_rows}
        )

        transaction_query = select(CanonicalTransaction).where(
            CanonicalTransaction.source_lineage == LINEAGE["POS"],
            scope_filter,
        )
        await accumulate_reconciliation_amounts(
            db,
            transaction_query,
            lambda transaction: (
                transaction.store_id,
                transaction.currency,
                transaction.business_date,
            ),
            transaction_totals,
            max_groups=MAX_SETTLEMENT_GROUPS,
            group_description="control manifest totals",
        )

    for manifest, store, business_date, currency, declared_count, declared_total in manifests:
        key = (store, currency, business_date)
        count = transaction_counts.get(key, 0)
        total = transaction_totals.get(key, Decimal("0"))
        if count < declared_count:
            gap_min, gap_share = (
                (policy.gap_min_count, policy.gap_share) if policy else (10, Decimal("0.1"))
            )
            if declared_count - count >= max(gap_min, declared_count * gap_share):
                add(
                    "MISSING_TRAN_BIG_GAP",
                    store,
                    business_date,
                    abs(declared_total - total),
                    currency,
                )
            add("INCOMPLETE_FEED", store, business_date, abs(declared_total - total), currency)
        elif count != declared_count or total != declared_total:
            add("BALANCING_VARIANCE", store, business_date, abs(total - declared_total), currency)
        manifest.status = "Processed"


async def run_settlement_controls(
    db, txns, add, amount_tolerance=Decimal("0"), tolerance_currency="USD"
):
    """Reconcile settlement batches when references exist, otherwise retain store/day controls."""
    bank = [t for t in txns if t.source_lineage == LINEAGE["Bank"]]
    referenced_banks = [t for t in bank if t.settlement_reference]
    if referenced_banks:
        await reconcile_referenced_bank_batches(
            db, referenced_banks, add, amount_tolerance, tolerance_currency
        )
    controls = (
        ("Bank", "BANK_VARIANCE", "MISSING_DEPOSIT", "Processor"),
        ("ERP", "GL_VARIANCE", "MISSING_GL_POSTING", "POS"),
    )
    for feed_key, variance_code, missing_code, basis in controls:
        feed = [
            txn
            for txn in txns
            if txn.source_lineage == LINEAGE[feed_key]
            and (feed_key != "Bank" or not txn.settlement_reference)
        ]
        if feed:
            await reconcile_daily_feed(
                db,
                feed,
                basis,
                variance_code,
                missing_code,
                add,
                amount_tolerance,
                tolerance_currency,
            )


async def reconcile_referenced_bank_batches(
    db, referenced_banks, add, amount_tolerance, tolerance_currency
):
    """Compare referenced bank deposits with matched processor settlements."""
    expected, actual = defaultdict(Decimal), defaultdict(Decimal)
    scopes = sorted({(row.store_id, row.currency, row.business_date) for row in referenced_banks})
    # Three bind parameters per scope keep these queries below SQLite's traditional limit.
    for offset in range(0, len(scopes), COMPOSITE_SCOPE_BATCH_SIZE):
        scope_batch = scopes[offset : offset + COMPOSITE_SCOPE_BATCH_SIZE]
        await accumulate_reconciliation_amounts(
            db,
            select(CanonicalTransaction).where(
                CanonicalTransaction.source_lineage == LINEAGE["Processor"],
                CanonicalTransaction.reconciliation_status == "Matched",
                tuple_(
                    CanonicalTransaction.store_id,
                    CanonicalTransaction.currency,
                    CanonicalTransaction.settlement_date,
                ).in_(scope_batch),
                CanonicalTransaction.settlement_reference.is_not(None),
            ),
            lambda row: (row.store_id, row.currency, row.settlement_date, row.settlement_reference),
            expected,
            max_groups=MAX_SETTLEMENT_GROUPS,
            group_description="processor settlement totals",
        )
        await accumulate_reconciliation_amounts(
            db,
            select(CanonicalTransaction).where(
                CanonicalTransaction.source_lineage == LINEAGE["Bank"],
                tuple_(
                    CanonicalTransaction.store_id,
                    CanonicalTransaction.currency,
                    CanonicalTransaction.business_date,
                ).in_(scope_batch),
                CanonicalTransaction.settlement_reference.is_not(None),
            ),
            lambda row: (row.store_id, row.currency, row.business_date, row.settlement_reference),
            actual,
            max_groups=MAX_SETTLEMENT_GROUPS,
            group_description="bank settlement totals",
        )
    report_referenced_bank_variances(expected, actual, add, amount_tolerance, tolerance_currency)
    mark_referenced_bank_deposits(
        referenced_banks, expected, actual, amount_tolerance, tolerance_currency
    )


def report_referenced_bank_variances(expected, actual, add, amount_tolerance, tolerance_currency):
    """Emit one finding per referenced settlement with an out-of-tolerance variance."""
    for key in expected.keys() | actual.keys():
        variance = expected.get(key, Decimal("0")) - actual.get(key, Decimal("0"))
        tolerance = amount_tolerance if key[1] == tolerance_currency else Decimal("0")
        if abs(variance) > tolerance:
            code = "MISSING_DEPOSIT" if key not in actual else "BANK_VARIANCE"
            add(code, key[0], key[2], abs(variance), key[1])


def mark_referenced_bank_deposits(
    referenced_banks, expected, actual, amount_tolerance, tolerance_currency
):
    """Set referenced deposit reconciliation state from its batch comparison."""
    for row in referenced_banks:
        key = (row.store_id, row.currency, row.business_date, row.settlement_reference)
        tolerance = amount_tolerance if row.currency == tolerance_currency else Decimal("0")
        balanced = abs(expected.get(key, Decimal("0")) - actual.get(key, Decimal("0"))) <= tolerance
        row.reconciliation_status, row.disposition = (
            ("Matched", "Closed") if balanced else ("Exception", "Awaiting")
        )


async def reconcile_daily_feed(
    db, feed, basis, variance_code, missing_code, add, amount_tolerance, tolerance_currency
):
    """Compare unreferenced bank or ERP totals with matched source transactions."""
    actual, expected = await collect_daily_feed_amounts(db, feed, basis)
    feed_lineage = feed[0].source_lineage
    unreferenced_only = all(row.settlement_reference is None for row in feed)
    scope_statuses = {}
    for key in expected.keys() | actual.keys():
        store, currency, business_date = key
        expected_amount, feed_amount = expected.get(key, Decimal("0")), actual.get(key)
        if feed_amount is None:
            if expected_amount != 0:
                add(missing_code, store, business_date, abs(expected_amount), currency)
            continue
        tolerance = amount_tolerance if currency == tolerance_currency else Decimal("0")
        balanced = abs(expected_amount - feed_amount) <= tolerance
        if not balanced:
            add(variance_code, store, business_date, abs(expected_amount - feed_amount), currency)
        scope_statuses[key] = "Matched" if balanced else "Exception"

    await update_daily_feed_statuses(db, feed_lineage, scope_statuses, unreferenced_only)
    for row in feed:
        status = scope_statuses.get((row.store_id, row.currency, row.business_date))
        if status:
            row.reconciliation_status = status
            row.disposition = "Closed" if status == "Matched" else "Awaiting"


async def collect_daily_feed_amounts(db, feed, basis):
    """Aggregate actual feed values and matched source values in bounded scope batches."""
    actual, expected = defaultdict(Decimal), defaultdict(Decimal)
    if not feed:
        return actual, expected
    scopes = sorted({(row.store_id, row.currency, row.business_date) for row in feed})
    unreferenced_only = all(row.settlement_reference is None for row in feed)
    for offset in range(0, len(scopes), COMPOSITE_SCOPE_BATCH_SIZE):
        scope_batch = scopes[offset : offset + COMPOSITE_SCOPE_BATCH_SIZE]
        feed_query = select(CanonicalTransaction).where(
            CanonicalTransaction.source_lineage == feed[0].source_lineage,
            tuple_(
                CanonicalTransaction.store_id,
                CanonicalTransaction.currency,
                CanonicalTransaction.business_date,
            ).in_(scope_batch),
        )
        if unreferenced_only:
            feed_query = feed_query.where(CanonicalTransaction.settlement_reference.is_(None))
        await accumulate_reconciliation_amounts(
            db,
            feed_query,
            lambda row: (row.store_id, row.currency, row.business_date),
            actual,
            max_groups=MAX_SETTLEMENT_GROUPS,
            group_description="daily feed totals",
        )

    # Restrict reads to represented feed scopes and keep all-dates scans bounded.
    for offset in range(0, len(scopes), COMPOSITE_SCOPE_BATCH_SIZE):
        scope_batch = scopes[offset : offset + COMPOSITE_SCOPE_BATCH_SIZE]
        expected_query = select(CanonicalTransaction).where(
            CanonicalTransaction.source_lineage == LINEAGE[basis],
            CanonicalTransaction.reconciliation_status == "Matched",
            tuple_(
                CanonicalTransaction.store_id,
                CanonicalTransaction.currency,
                CanonicalTransaction.business_date,
            ).in_(scope_batch),
        )
        await accumulate_reconciliation_amounts(
            db,
            expected_query,
            lambda row: (row.store_id, row.currency, row.business_date),
            expected,
            max_groups=MAX_SETTLEMENT_GROUPS,
            group_description="daily source totals",
        )
    return actual, expected


async def update_daily_feed_statuses(db, feed_lineage, scope_statuses, unreferenced_only):
    """Update every feed row represented by the recalculated daily scopes in bounded batches."""
    scopes_by_status = defaultdict(list)
    for scope, status in scope_statuses.items():
        scopes_by_status[status].append(scope)

    for status, scopes in scopes_by_status.items():
        disposition = "Closed" if status == "Matched" else "Awaiting"
        for offset in range(0, len(scopes), COMPOSITE_SCOPE_BATCH_SIZE):
            scope_batch = scopes[offset : offset + COMPOSITE_SCOPE_BATCH_SIZE]
            statement = update(CanonicalTransaction).where(
                CanonicalTransaction.source_lineage == feed_lineage,
                tuple_(
                    CanonicalTransaction.store_id,
                    CanonicalTransaction.currency,
                    CanonicalTransaction.business_date,
                ).in_(scope_batch),
                CanonicalTransaction.reconciliation_status.in_(
                    ("Unmatched", "Matched", "Exception")
                ),
            )
            if unreferenced_only:
                statement = statement.where(CanonicalTransaction.settlement_reference.is_(None))
            await db.execute(
                statement.values(
                    reconciliation_status=status,
                    disposition=disposition,
                ).execution_options(synchronize_session=False)
            )
            for row in db.identity_map.values():
                if (
                    isinstance(row, CanonicalTransaction)
                    and row.source_lineage == feed_lineage
                    and (row.store_id, row.currency, row.business_date) in scope_batch
                    and (not unreferenced_only or row.settlement_reference is None)
                    and row.reconciliation_status in ("Unmatched", "Matched", "Exception")
                ):
                    row.reconciliation_status = status
                    row.disposition = disposition


async def accumulate_reconciliation_amounts(
    db,
    query,
    key_for,
    totals,
    *,
    max_groups: int,
    group_description: str,
) -> None:
    """Aggregate corrected amounts in bounded primary-key pages."""
    last_id = None
    while True:
        page_query = query
        if last_id is not None:
            page_query = page_query.where(CanonicalTransaction.id > last_id)
        rows = (
            (
                await db.execute(
                    page_query.order_by(CanonicalTransaction.id).limit(SETTLEMENT_QUERY_PAGE_SIZE)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return
        amounts = await effective_amounts(db, rows)
        for row in rows:
            key = key_for(row)
            if key not in totals and len(totals) >= max_groups:
                raise ReconciliationCapacityError(
                    f"{group_description.capitalize()} exceed the {max_groups:,}-group working limit; "
                    "narrow the reconciliation date scope."
                )
            totals[key] += amounts[row.id]
        last_id = rows[-1].id
