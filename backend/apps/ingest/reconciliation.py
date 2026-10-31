"""Deterministic transaction matching and sales audit control evaluation."""
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
import json
import uuid

from sqlalchemy import and_, func, or_, select, text, tuple_, update

from apps.cases.identifiers import generate_case_number
from apps.corrections.effects import effective_amounts
from apps.ingest.common import (
    COMPOSITE_SCOPE_BATCH_SIZE, LINEAGE, MAX_SETTLEMENT_GROUPS,
    RECONCILIATION_LOCK_KEY, SETTLEMENT_QUERY_PAGE_SIZE,
    SHOPIFY_ORDER_KEY_BATCH_SIZE, ReconciliationCapacityError,
)
from apps.ingest.limits import MAX_RECONCILIATION_TRANSACTIONS
from apps.ingest.manifests import iter_manifests, validate_business_date
from apps.transactions import detail as transaction_detail
from integrations.ontology import ontology
from integrations.ontology.nimbus import audit, sla_due
from apps.transactions.models import (
    CanonicalTransaction,
    TransactionDiscount,
    TransactionTender,
)
from apps.cases.models import (
    Case,
    Exception as DBException,
)
from apps.ingest.models import ReconciliationPolicy, SourceRecord


async def lock_reconciliation(db) -> None:
    """Serialize reconciliation runs, including manifest-only scopes with no row locks."""
    if db.get_bind().dialect.name == "postgresql":
        await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": RECONCILIATION_LOCK_KEY})


async def reconcile(
    db,
    business_date: str | None = None,
    *,
    store_ids: set[str] | None = None,
) -> dict:
    if business_date is not None:
        validate_business_date(business_date)
    await lock_reconciliation(db)
    q = select(CanonicalTransaction).where(CanonicalTransaction.reconciliation_status == "Unmatched")
    if business_date:
        q = q.where(CanonicalTransaction.business_date == business_date)
    if store_ids is not None:
        q = q.where(CanonicalTransaction.store_id.in_(store_ids))
    q = (q.order_by(CanonicalTransaction.id)
         .limit(MAX_RECONCILIATION_TRANSACTIONS + 1)
         .with_for_update()
         .execution_options(populate_existing=True))
    txns = (await db.execute(q)).scalars().all()
    if len(txns) > MAX_RECONCILIATION_TRANSACTIONS:
        scope = f" for {business_date}" if business_date else " across all business dates"
        raise ReconciliationCapacityError(
            f"Reconciliation{scope} exceeds the {MAX_RECONCILIATION_TRANSACTIONS:,}-transaction limit; "
            "resolve a smaller date scope or reduce the unmatched backlog."
        )
    policy = (await db.execute(select(ReconciliationPolicy).where(ReconciliationPolicy.active == True)
                               .order_by(ReconciliationPolicy.updated_at.desc(), ReconciliationPolicy.id.desc())
                               .limit(1))).scalar_one_or_none()  # noqa: E712
    amount_tolerance = policy.amount_tolerance if policy else Decimal("0")
    tolerance_currency = policy.amount_tolerance_currency if policy else "USD"
    findings, matched = await match_processor_transactions(db, txns, amount_tolerance, tolerance_currency,
                                                           policy.settlement_day_tolerance if policy else 0)

    def add(etype, store, bdate, amount, currency):
        findings.append((etype, store, bdate, amount, [], currency))

    def flag(etype, amount, *group):
        findings.append((etype, group[0].store_id, group[0].business_date, amount, [t.id for t in group], group[0].currency))
        for t in group:
            t.reconciliation_status, t.disposition = "Exception", "Awaiting"

    await reconcile_shopify(db, txns, flag)
    await run_transaction_audit(db, txns, flag)
    await run_controls(db, business_date, add, policy, store_ids=store_ids)
    await run_settlement_controls(db, txns, add, amount_tolerance, tolerance_currency)

    counts, cases = create_reconciliation_cases(db, findings)
    return {"transactions_examined": len(txns), "matched_pairs": matched,
            "exceptions": dict(counts), "cases_created": cases}


async def match_processor_transactions(db, transactions, amount_tolerance, tolerance_currency,
                                       settlement_day_tolerance):
    """Match POS transactions to processor activity and mark unmatched findings."""
    amounts = await effective_amounts(db, transactions)
    pos = sorted((t for t in transactions if t.source_lineage == LINEAGE["POS"]),
                 key=lambda t: t.event_timestamp)
    processors = [t for t in transactions if t.source_lineage == LINEAGE["Processor"]]
    tenders = await load_tenders(db, pos)
    processor_index = defaultdict(list)
    for processor in processors:
        if processor.payment_reference:
            key = (processor.store_id, processor.currency, processor.payment_reference,
                   processor.transaction_type)
            processor_index[key].append(processor)

    findings, matched, used_processors = [], 0, set()

    def flag(exception_type, amount, *group):
        findings.append((exception_type, group[0].store_id, group[0].business_date, amount,
                         [transaction.id for transaction in group], group[0].currency))
        for transaction in group:
            transaction.reconciliation_status, transaction.disposition = "Exception", "Awaiting"

    duplicate_sales = flag_duplicate_sales(pos, amounts, flag)

    for transaction in pos:
        if transaction.id in duplicate_sales:
            continue
        matched += match_pos_transaction(
            transaction, amounts, tenders, processor_index, used_processors, flag,
            amount_tolerance, tolerance_currency, settlement_day_tolerance)

    for processor in processors:
        if processor.id not in used_processors:
            flag("ORPHAN_PAYMENT", abs(amounts[processor.id]), processor)
    return findings, matched


def flag_duplicate_sales(pos_transactions, amounts, flag):
    """Mark duplicate POS sale identities and return the duplicate transaction IDs."""
    seen, duplicates = set(), set()
    for transaction in pos_transactions:
        if transaction.transaction_type != "Sale" or not transaction.payment_reference:
            continue
        key = (transaction.store_id, transaction.business_date, transaction.register_id,
               transaction.currency, amounts[transaction.id], transaction.payment_reference)
        if key in seen:
            duplicates.add(transaction.id)
            flag("DUPLICATE_SALES", abs(amounts[transaction.id]), transaction)
        seen.add(key)
    return duplicates


async def load_tenders(db, pos_transactions):
    """Load tenders in bounded batches for POS transactions."""
    tenders = defaultdict(list)
    pos_ids = [transaction.id for transaction in pos_transactions]
    for offset in range(0, len(pos_ids), 500):
        query = select(TransactionTender).where(
            TransactionTender.transaction_id.in_(pos_ids[offset:offset + 500]))
        for tender in (await db.execute(query)).scalars().all():
            tenders[tender.transaction_id].append(
                {"tender_type": tender.tender_type, "amount": tender.amount})
    return tenders


def match_pos_transaction(transaction, amounts, tenders, processor_index, used_processors, flag,
                           amount_tolerance, tolerance_currency, settlement_day_tolerance):
    """Match one POS transaction and return one when a counterpart is accepted."""
    expected = transaction_detail.card_amount(tenders.get(transaction.id, []), amounts[transaction.id])
    if tenders.get(transaction.id) and expected == 0:
        transaction.reconciliation_status, transaction.disposition = "Matched", "Closed"
        return 1

    expected_type = "Capture" if transaction.transaction_type == "Sale" else "Refund"
    key = (transaction.store_id, transaction.currency, transaction.payment_reference, expected_type)
    candidates = processor_index.get(key, []) if transaction.payment_reference else []
    if len(candidates) != 1:
        exception_type = "MISSING_REFUND" if transaction.transaction_type == "Return" else "UNMATCHED_SALE"
        flag(exception_type, abs(amounts[transaction.id]), transaction)
        return 0

    processor = candidates.pop()
    used_processors.add(processor.id)
    tolerance = amount_tolerance if transaction.currency == tolerance_currency else Decimal("0")
    if abs(amounts[processor.id] - expected) > tolerance:
        flag("AMOUNT_MISMATCH", abs(amounts[processor.id] - expected), transaction, processor)
        return 0
    settlement_delay = (datetime.fromisoformat(processor.settlement_date).date()
                        - datetime.fromisoformat(transaction.business_date).date()).days
    if settlement_delay > settlement_day_tolerance:
        flag("TIMING_DIFFERENCE", abs(amounts[transaction.id]), transaction, processor)
        return 0
    for matched_transaction in (transaction, processor):
        matched_transaction.reconciliation_status, matched_transaction.disposition = "Matched", "Closed"
    return 1


def create_reconciliation_cases(db, findings: list[tuple]) -> tuple[dict, int]:
    """Group findings and persist their cases, exceptions, and audit events."""
    groups = defaultdict(list)
    for finding in findings:
        groups[(finding[0], finding[1], finding[2], finding[5])].append(finding)

    counts, cases = defaultdict(int), 0
    for (exception_type, store, business_date, currency), items in groups.items():
        definition = ontology.get(exception_type)
        amount = sum((item[3] for item in items), Decimal("0"))
        exposure = Decimal("0") if definition["exposure"] == "zero" else amount
        case_id = str(uuid.uuid4())
        priority = "Critical" if amount > 3000 else "High" if amount > 1000 else "Normal"
        db.add(Case(
            id=case_id, case_number=generate_case_number(business_date), status="Open",
            case_type=definition["label"], business_date=business_date, store_id=store, currency=currency,
            total_exception_amount=amount, total_exposure=exposure, investigation_status="Pending",
            evidence_completeness=0, priority=priority, sla_due_at=sla_due(priority)))
        db.add(DBException(
            id=str(uuid.uuid4()), case_id=case_id, exception_type=exception_type,
            exception_family=definition["family"], source_system="Reconciliation",
            detection_origin="Reconciliation", detection_rule_id=f"RECON-{exception_type}",
            exception_amount=amount, estimated_exposure=exposure,
            severity="High" if amount > 1000 else "Medium", confidence="High",
            close_impact="Yes" if amount else "No"))
        audit(db, "CASE_CREATED", case_id, "Case", case_id,
              f"Reconciliation raised {definition['label']} ({len(items)} item(s), {amount})",
              actor="reconciliation-engine",
              after={"transaction_ids": [transaction_id for item in items for transaction_id in item[4]]})
        counts[exception_type] += len(items)
        cases += 1
    return dict(counts), cases


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
            key, group, group_amounts, historical_refund_totals,
            orders_by_key, order_amounts, flag,
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
        key_batch = order_keys[offset:offset + SHOPIFY_ORDER_KEY_BATCH_SIZE]
        orders = await load_earliest_shopify_orders(db, key_batch)
        orders_by_key.update({
            (order.store_id, order.currency, order.payment_reference): order
            for order in orders
        })
        await accumulate_shopify_refund_history(db, key_batch, through_dates, historical_refund_totals)
    return orders_by_key, historical_refund_totals


async def load_earliest_shopify_orders(db, key_batch):
    """Lock the earliest event-time order for every requested identity."""
    key_filter = tuple_(
        CanonicalTransaction.store_id,
        CanonicalTransaction.currency,
        CanonicalTransaction.payment_reference,
    ).in_(key_batch)
    ranked_orders = select(
        CanonicalTransaction.id.label("id"),
        func.row_number().over(
            partition_by=(
                CanonicalTransaction.store_id,
                CanonicalTransaction.currency,
                CanonicalTransaction.payment_reference,
            ),
            order_by=(CanonicalTransaction.event_timestamp, CanonicalTransaction.id),
        ).label("order_rank"),
    ).where(
        CanonicalTransaction.source_lineage == LINEAGE["Shopify"],
        CanonicalTransaction.transaction_type == "Order",
        key_filter,
    ).subquery()
    return (await db.execute(
        select(CanonicalTransaction).join(
            ranked_orders, CanonicalTransaction.id == ranked_orders.c.id
        ).where(ranked_orders.c.order_rank == 1).order_by(CanonicalTransaction.id).with_for_update()
        .execution_options(populate_existing=True)
    )).scalars().all()


async def accumulate_shopify_refund_history(db, key_batch, through_dates, totals):
    """Accumulate historical refunds through the current reconciliation dates."""
    historical_refund_query = select(CanonicalTransaction).where(
        CanonicalTransaction.source_lineage == LINEAGE["Shopify"],
        CanonicalTransaction.transaction_type == "Refund",
        or_(*(
            and_(
                CanonicalTransaction.store_id == store,
                CanonicalTransaction.currency == currency,
                CanonicalTransaction.payment_reference == reference,
                CanonicalTransaction.business_date <= through_dates[(store, currency, reference)],
            )
            for store, currency, reference in key_batch
        )),
    )
    await accumulate_reconciliation_amounts(
        db,
        historical_refund_query,
        lambda refund: (refund.store_id, refund.currency, refund.payment_reference),
        totals,
        max_groups=MAX_RECONCILIATION_TRANSACTIONS,
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
        refunded.update((await db.execute(select(CanonicalTransaction.store_id, CanonicalTransaction.currency,
            CanonicalTransaction.payment_reference).where(
                CanonicalTransaction.source_lineage == LINEAGE["Shopify"], CanonicalTransaction.transaction_type == "Refund",
                CanonicalTransaction.payment_reference.in_(refs[offset:offset + 500])).distinct())).all())
    source_ids = list({o.source_record_id for o in orders if o.source_record_id})
    srs = {}
    for offset in range(0, len(source_ids), 500):
        rows = (await db.execute(select(SourceRecord).where(
            SourceRecord.id.in_(source_ids[offset:offset + 500])))).scalars().all()
        srs.update({sr.id: sr for sr in rows})
    for o in orders:
        sr = srs.get(o.source_record_id)
        if not o.payment_reference or (o.store_id, o.currency, o.payment_reference) in refunded or sr is None:
            continue
        if json.loads(sr.payload).get("financial_status") == "paid":
            o.reconciliation_status, o.disposition = "Matched", "Closed"


async def run_transaction_audit(db, txns, flag):
    """Per-transaction audit errors from stored detail: out of balance, and a manual discount with no reason."""
    pos = [t for t in txns if t.source_lineage == LINEAGE["POS"] and t.reconciliation_status != "Exception"]
    for t in pos:
        if t.detail_status == "Unbalanced":
            flag("TRAN_OUT_BAL", abs(t.signed_amount), t)
    ids = [t.id for t in pos]
    by_id = {t.id: t for t in pos}
    for i in range(0, len(ids), 500):
        rows = (await db.execute(select(TransactionDiscount).where(
            TransactionDiscount.transaction_id.in_(ids[i:i + 500]), TransactionDiscount.kind == "Manual"))).scalars().all()
        for d in rows:
            if not (d.description or "").strip():
                flag("DISC_REASON_REQ", Decimal("0"), by_id[d.transaction_id])


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
        batch.append((m, store, bd, d.get("currency", "USD"), int(d["declared_count"]),
                      Decimal(str(d["declared_total"]))))
        if len(batch) == COMPOSITE_SCOPE_BATCH_SIZE:
            await reconcile_control_manifest_batch(db, batch, add, policy)
            batch.clear()
    if batch:
        await reconcile_control_manifest_batch(db, batch, add, policy)


async def reconcile_control_manifest_batch(db, manifests, add, policy=None):
    """Compare a bounded group of control manifests using grouped counts and paged corrected totals."""
    scopes = sorted({(store, currency, business_date)
                     for _manifest, store, business_date, currency, _count, _total in manifests})
    transaction_counts = {}
    transaction_totals: dict[tuple[str, str, str], Decimal] = defaultdict(Decimal)

    for offset in range(0, len(scopes), COMPOSITE_SCOPE_BATCH_SIZE):
        scope_batch = scopes[offset:offset + COMPOSITE_SCOPE_BATCH_SIZE]
        scope_filter = tuple_(
            CanonicalTransaction.store_id,
            CanonicalTransaction.currency,
            CanonicalTransaction.business_date,
        ).in_(scope_batch)
        count_rows = (await db.execute(
            select(
                CanonicalTransaction.store_id,
                CanonicalTransaction.currency,
                CanonicalTransaction.business_date,
                func.count(),
            ).where(
                CanonicalTransaction.source_lineage == LINEAGE["POS"],
                scope_filter,
            ).group_by(
                CanonicalTransaction.store_id,
                CanonicalTransaction.currency,
                CanonicalTransaction.business_date,
            )
        )).all()
        transaction_counts.update({(store, currency, day): count
                                   for store, currency, day, count in count_rows})

        transaction_query = select(CanonicalTransaction).where(
            CanonicalTransaction.source_lineage == LINEAGE["POS"],
            scope_filter,
        )
        await accumulate_reconciliation_amounts(
            db,
            transaction_query,
            lambda transaction: (transaction.store_id, transaction.currency, transaction.business_date),
            transaction_totals,
            max_groups=MAX_SETTLEMENT_GROUPS,
        )

    for manifest, store, business_date, currency, declared_count, declared_total in manifests:
        key = (store, currency, business_date)
        count = transaction_counts.get(key, 0)
        total = transaction_totals.get(key, Decimal("0"))
        if count < declared_count:
            gap_min, gap_share = (policy.gap_min_count, policy.gap_share) if policy else (10, Decimal("0.1"))
            if declared_count - count >= max(gap_min, declared_count * gap_share):
                add("MISSING_TRAN_BIG_GAP", store, business_date, abs(declared_total - total), currency)
            add("INCOMPLETE_FEED", store, business_date, abs(declared_total - total), currency)
        elif count != declared_count or total != declared_total:
            add("BALANCING_VARIANCE", store, business_date, abs(total - declared_total), currency)
        manifest.status = "Processed"


async def run_settlement_controls(db, txns, add, amount_tolerance=Decimal("0"), tolerance_currency="USD"):
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
            txn for txn in txns
            if txn.source_lineage == LINEAGE[feed_key]
            and (feed_key != "Bank" or not txn.settlement_reference)
        ]
        if feed:
            await reconcile_daily_feed(
                db, feed, basis, variance_code, missing_code, add,
                amount_tolerance, tolerance_currency,
            )


async def reconcile_referenced_bank_batches(db, referenced_banks, add, amount_tolerance, tolerance_currency):
    """Compare referenced bank deposits with matched processor settlements."""
    expected, actual = defaultdict(Decimal), defaultdict(Decimal)
    scopes = sorted({(row.store_id, row.currency, row.business_date) for row in referenced_banks})
    # Three bind parameters per scope keep these queries below SQLite's traditional limit.
    for offset in range(0, len(scopes), COMPOSITE_SCOPE_BATCH_SIZE):
        scope_batch = scopes[offset:offset + COMPOSITE_SCOPE_BATCH_SIZE]
        await accumulate_reconciliation_amounts(
            db,
            select(CanonicalTransaction).where(
                CanonicalTransaction.source_lineage == LINEAGE["Processor"],
                CanonicalTransaction.reconciliation_status == "Matched",
                tuple_(CanonicalTransaction.store_id, CanonicalTransaction.currency,
                       CanonicalTransaction.settlement_date).in_(scope_batch),
                CanonicalTransaction.settlement_reference.is_not(None),
            ),
            lambda row: (row.store_id, row.currency, row.settlement_date, row.settlement_reference),
            expected,
            max_groups=MAX_SETTLEMENT_GROUPS,
        )
        await accumulate_reconciliation_amounts(
            db,
            select(CanonicalTransaction).where(
                CanonicalTransaction.source_lineage == LINEAGE["Bank"],
                tuple_(CanonicalTransaction.store_id, CanonicalTransaction.currency,
                       CanonicalTransaction.business_date).in_(scope_batch),
                CanonicalTransaction.settlement_reference.is_not(None),
            ),
            lambda row: (row.store_id, row.currency, row.business_date, row.settlement_reference),
            actual,
            max_groups=MAX_SETTLEMENT_GROUPS,
        )
    report_referenced_bank_variances(expected, actual, add, amount_tolerance, tolerance_currency)
    mark_referenced_bank_deposits(referenced_banks, expected, actual, amount_tolerance, tolerance_currency)


def report_referenced_bank_variances(expected, actual, add, amount_tolerance, tolerance_currency):
    """Emit one finding per referenced settlement with an out-of-tolerance variance."""
    for key in expected.keys() | actual.keys():
        variance = expected.get(key, Decimal("0")) - actual.get(key, Decimal("0"))
        tolerance = amount_tolerance if key[1] == tolerance_currency else Decimal("0")
        if abs(variance) > tolerance:
            code = "MISSING_DEPOSIT" if key not in actual else "BANK_VARIANCE"
            add(code, key[0], key[2], abs(variance), key[1])


def mark_referenced_bank_deposits(referenced_banks, expected, actual, amount_tolerance, tolerance_currency):
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
    scopes = sorted({(row.store_id, row.currency, row.business_date) for row in feed})
    feed_query = select(CanonicalTransaction).where(
        CanonicalTransaction.source_lineage == feed[0].source_lineage,
        tuple_(CanonicalTransaction.store_id, CanonicalTransaction.currency,
               CanonicalTransaction.business_date).in_(scopes),
    )
    if all(row.settlement_reference is None for row in feed):
        feed_query = feed_query.where(CanonicalTransaction.settlement_reference.is_(None))
    await accumulate_reconciliation_amounts(
        db,
        feed_query,
        lambda row: (row.store_id, row.currency, row.business_date),
        actual,
        max_groups=MAX_SETTLEMENT_GROUPS,
    )

    # Restrict reads to represented feed scopes and keep all-dates scans bounded.
    for offset in range(0, len(scopes), 250):
        scope_batch = scopes[offset:offset + 250]
        expected_query = select(CanonicalTransaction).where(
            CanonicalTransaction.source_lineage == LINEAGE[basis],
            CanonicalTransaction.reconciliation_status == "Matched",
            tuple_(CanonicalTransaction.store_id, CanonicalTransaction.currency,
                   CanonicalTransaction.business_date).in_(scope_batch),
        )
        await accumulate_reconciliation_amounts(
            db,
            expected_query,
            lambda row: (row.store_id, row.currency, row.business_date),
            expected,
            max_groups=MAX_SETTLEMENT_GROUPS,
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
            scope_batch = scopes[offset:offset + COMPOSITE_SCOPE_BATCH_SIZE]
            statement = update(CanonicalTransaction).where(
                CanonicalTransaction.source_lineage == feed_lineage,
                tuple_(CanonicalTransaction.store_id, CanonicalTransaction.currency,
                       CanonicalTransaction.business_date).in_(scope_batch),
                CanonicalTransaction.reconciliation_status.in_(
                    ("Unmatched", "Matched", "Exception")
                ),
            )
            if unreferenced_only:
                statement = statement.where(CanonicalTransaction.settlement_reference.is_(None))
            await db.execute(statement.values(
                reconciliation_status=status,
                disposition=disposition,
            ).execution_options(synchronize_session=False))
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


async def accumulate_reconciliation_amounts(db, query, key_for, totals, *, max_groups: int) -> None:
    """Aggregate corrected amounts in bounded primary-key pages."""
    last_id = None
    while True:
        page_query = query
        if last_id is not None:
            page_query = page_query.where(CanonicalTransaction.id > last_id)
        rows = (await db.execute(page_query.order_by(CanonicalTransaction.id)
                                 .limit(SETTLEMENT_QUERY_PAGE_SIZE))).scalars().all()
        if not rows:
            return
        amounts = await effective_amounts(db, rows)
        for row in rows:
            key = key_for(row)
            if key not in totals and len(totals) >= max_groups:
                raise ReconciliationCapacityError(
                    f"Settlement reconciliation exceeds the {max_groups:,}-group working limit; "
                    "narrow the reconciliation date scope."
                )
            totals[key] += amounts[row.id]
        last_id = rows[-1].id
