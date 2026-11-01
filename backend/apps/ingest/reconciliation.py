"""Deterministic transaction matching and sales audit control evaluation."""

import json
import uuid
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from integrations.ontology import ontology
from integrations.ontology.nimbus import audit, sla_due
from sqlalchemy import and_, func, or_, select, text, tuple_

from apps.cases.identifiers import generate_case_number
from apps.cases.models import (
    Case,
)
from apps.cases.models import (
    Exception as DBException,
)
from apps.corrections.effects import effective_amounts
from apps.ingest.common import (
    LINEAGE,
    RECONCILIATION_LOCK_KEY,
    SHOPIFY_ORDER_KEY_BATCH_SIZE,
    ReconciliationCapacityError,
)
from apps.ingest.limits import MAX_RECONCILIATION_TRANSACTIONS
from apps.ingest.manifests import validate_business_date
from apps.ingest.models import ReconciliationPolicy, SourceRecord
from apps.transactions import detail as transaction_detail
from apps.transactions.models import (
    CanonicalTransaction,
    TransactionDiscount,
    TransactionTender,
)


async def lock_reconciliation(db) -> None:
    """Serialize reconciliation runs, including manifest-only scopes with no row locks."""
    if db.get_bind().dialect.name == "postgresql":
        await db.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": RECONCILIATION_LOCK_KEY}
        )


async def reconcile(
    db,
    business_date: str | None = None,
    *,
    store_ids: set[str] | None = None,
) -> dict:
    if business_date is not None:
        validate_business_date(business_date)
    await lock_reconciliation(db)
    q = select(CanonicalTransaction).where(
        CanonicalTransaction.reconciliation_status == "Unmatched"
    )
    if business_date:
        q = q.where(CanonicalTransaction.business_date == business_date)
    if store_ids is not None:
        q = q.where(CanonicalTransaction.store_id.in_(store_ids))
    q = (
        q.order_by(CanonicalTransaction.id)
        .limit(MAX_RECONCILIATION_TRANSACTIONS + 1)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    txns = (await db.execute(q)).scalars().all()
    if len(txns) > MAX_RECONCILIATION_TRANSACTIONS:
        scope = f" for {business_date}" if business_date else " across all business dates"
        raise ReconciliationCapacityError(
            f"Reconciliation{scope} exceeds the {MAX_RECONCILIATION_TRANSACTIONS:,}-transaction limit; "
            "resolve a smaller date scope or reduce the unmatched backlog."
        )
    policy = (
        await db.execute(
            select(ReconciliationPolicy)
            .where(ReconciliationPolicy.active.is_(True))
            .order_by(ReconciliationPolicy.updated_at.desc(), ReconciliationPolicy.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    amount_tolerance = policy.amount_tolerance if policy else Decimal("0")
    tolerance_currency = policy.amount_tolerance_currency if policy else "USD"
    findings, matched = await match_processor_transactions(
        db,
        txns,
        amount_tolerance,
        tolerance_currency,
        policy.settlement_day_tolerance if policy else 0,
    )

    def add(etype, store, bdate, amount, currency):
        findings.append((etype, store, bdate, amount, [], currency))

    def flag(etype, amount, *group):
        findings.append(
            (
                etype,
                group[0].store_id,
                group[0].business_date,
                amount,
                [t.id for t in group],
                group[0].currency,
            )
        )
        for t in group:
            t.reconciliation_status, t.disposition = "Exception", "Awaiting"

    await reconcile_shopify(db, txns, flag)
    await run_transaction_audit(db, txns, flag)
    await run_controls(db, business_date, add, policy, store_ids=store_ids)
    await run_settlement_controls(db, txns, add, amount_tolerance, tolerance_currency)

    counts, cases = create_reconciliation_cases(db, findings)
    return {
        "transactions_examined": len(txns),
        "matched_pairs": matched,
        "exceptions": dict(counts),
        "cases_created": cases,
    }


async def match_processor_transactions(
    db, transactions, amount_tolerance, tolerance_currency, settlement_day_tolerance
):
    """Match POS transactions to processor activity and mark unmatched findings."""
    amounts = await effective_amounts(db, transactions)
    pos = sorted(
        (t for t in transactions if t.source_lineage == LINEAGE["POS"]),
        key=lambda t: t.event_timestamp,
    )
    processors = [t for t in transactions if t.source_lineage == LINEAGE["Processor"]]
    tenders = await load_tenders(db, pos)
    processor_index = defaultdict(list)
    for processor in processors:
        if processor.payment_reference:
            key = (
                processor.store_id,
                processor.currency,
                processor.payment_reference,
                processor.transaction_type,
            )
            processor_index[key].append(processor)

    findings, matched, used_processors = [], 0, set()

    def flag(exception_type, amount, *group):
        findings.append(
            (
                exception_type,
                group[0].store_id,
                group[0].business_date,
                amount,
                [transaction.id for transaction in group],
                group[0].currency,
            )
        )
        for transaction in group:
            transaction.reconciliation_status, transaction.disposition = "Exception", "Awaiting"

    duplicate_sales = flag_duplicate_sales(pos, amounts, flag)

    for transaction in pos:
        if transaction.id in duplicate_sales:
            continue
        matched += match_pos_transaction(
            transaction,
            amounts,
            tenders,
            processor_index,
            used_processors,
            flag,
            amount_tolerance,
            tolerance_currency,
            settlement_day_tolerance,
        )

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
        key = (
            transaction.store_id,
            transaction.business_date,
            transaction.register_id,
            transaction.currency,
            amounts[transaction.id],
            transaction.payment_reference,
        )
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
            TransactionTender.transaction_id.in_(pos_ids[offset : offset + 500])
        )
        for tender in (await db.execute(query)).scalars().all():
            tenders[tender.transaction_id].append(
                {"tender_type": tender.tender_type, "amount": tender.amount}
            )
    return tenders


def match_pos_transaction(
    transaction,
    amounts,
    tenders,
    processor_index,
    used_processors,
    flag,
    amount_tolerance,
    tolerance_currency,
    settlement_day_tolerance,
):
    """Match one POS transaction and return one when a counterpart is accepted."""
    expected = transaction_detail.card_amount(
        tenders.get(transaction.id, []), amounts[transaction.id]
    )
    if tenders.get(transaction.id) and expected == 0:
        transaction.reconciliation_status, transaction.disposition = "Matched", "Closed"
        return 1

    expected_type = "Capture" if transaction.transaction_type == "Sale" else "Refund"
    key = (transaction.store_id, transaction.currency, transaction.payment_reference, expected_type)
    candidates = processor_index.get(key, []) if transaction.payment_reference else []
    if len(candidates) != 1:
        exception_type = (
            "MISSING_REFUND" if transaction.transaction_type == "Return" else "UNMATCHED_SALE"
        )
        flag(exception_type, abs(amounts[transaction.id]), transaction)
        return 0

    processor = candidates.pop()
    used_processors.add(processor.id)
    tolerance = amount_tolerance if transaction.currency == tolerance_currency else Decimal("0")
    if abs(amounts[processor.id] - expected) > tolerance:
        flag("AMOUNT_MISMATCH", abs(amounts[processor.id] - expected), transaction, processor)
        return 0
    settlement_delay = (
        datetime.fromisoformat(processor.settlement_date).date()
        - datetime.fromisoformat(transaction.business_date).date()
    ).days
    if settlement_delay > settlement_day_tolerance:
        flag("TIMING_DIFFERENCE", abs(amounts[transaction.id]), transaction, processor)
        return 0
    for matched_transaction in (transaction, processor):
        matched_transaction.reconciliation_status, matched_transaction.disposition = (
            "Matched",
            "Closed",
        )
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
        db.add(
            Case(
                id=case_id,
                case_number=generate_case_number(business_date),
                status="Open",
                case_type=definition["label"],
                business_date=business_date,
                store_id=store,
                currency=currency,
                total_exception_amount=amount,
                total_exposure=exposure,
                investigation_status="Pending",
                evidence_completeness=0,
                priority=priority,
                sla_due_at=sla_due(priority),
            )
        )
        db.add(
            DBException(
                id=str(uuid.uuid4()),
                case_id=case_id,
                exception_type=exception_type,
                exception_family=definition["family"],
                source_system="Reconciliation",
                detection_origin="Reconciliation",
                detection_rule_id=f"RECON-{exception_type}",
                exception_amount=amount,
                estimated_exposure=exposure,
                severity="High" if amount > 1000 else "Medium",
                confidence="High",
                close_impact="Yes" if amount else "No",
            )
        )
        audit(
            db,
            "CASE_CREATED",
            case_id,
            "Case",
            case_id,
            f"Reconciliation raised {definition['label']} ({len(items)} item(s), {amount})",
            actor="reconciliation-engine",
            after={
                "transaction_ids": [transaction_id for item in items for transaction_id in item[4]]
            },
        )
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


async def run_transaction_audit(db, txns, flag):
    """Per-transaction audit errors from stored detail: out of balance, and a manual discount with no reason."""
    pos = [
        t
        for t in txns
        if t.source_lineage == LINEAGE["POS"] and t.reconciliation_status != "Exception"
    ]
    for t in pos:
        if t.detail_status == "Unbalanced":
            flag("TRAN_OUT_BAL", abs(t.signed_amount), t)
    ids = [t.id for t in pos]
    by_id = {t.id: t for t in pos}
    for i in range(0, len(ids), 500):
        rows = (
            (
                await db.execute(
                    select(TransactionDiscount).where(
                        TransactionDiscount.transaction_id.in_(ids[i : i + 500]),
                        TransactionDiscount.kind == "Manual",
                    )
                )
            )
            .scalars()
            .all()
        )
        for d in rows:
            if not (d.description or "").strip():
                flag("DISC_REASON_REQ", Decimal("0"), by_id[d.transaction_id])


from apps.ingest.controls import (
    accumulate_reconciliation_amounts,
    collect_daily_feed_amounts,
    mark_referenced_bank_deposits,
    reconcile_control_manifest_batch,
    reconcile_daily_feed,
    reconcile_referenced_bank_batches,
    report_referenced_bank_variances,
    run_controls,
    run_settlement_controls,
    update_daily_feed_statuses,
)

__all__ = [
    "accumulate_reconciliation_amounts",
    "collect_daily_feed_amounts",
    "mark_referenced_bank_deposits",
    "reconcile_control_manifest_batch",
    "reconcile_daily_feed",
    "reconcile_referenced_bank_batches",
    "report_referenced_bank_variances",
    "run_controls",
    "run_settlement_controls",
    "update_daily_feed_statuses",
]
