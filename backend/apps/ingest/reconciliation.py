"""Deterministic transaction matching and sales audit control evaluation."""

import uuid
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from integrations.ontology import ontology
from integrations.ontology.nimbus import audit, sla_due
from sqlalchemy import select, text

from apps.cases.identifiers import generate_case_number
from apps.cases.models import (
    Case,
)
from apps.cases.models import (
    Exception as DBException,
)
from apps.corrections.effects import effective_amounts
from apps.ingest.audit_rules import run_detail_rules
from apps.ingest.common import (
    LINEAGE,
    RECONCILIATION_LOCK_KEY,
    ReconciliationCapacityError,
)
from apps.ingest.limits import MAX_RECONCILIATION_TRANSACTIONS
from apps.ingest.manifests import validate_business_date
from apps.ingest.models import ReconciliationPolicy
from apps.ingest.register_events import run_register_event_rules
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
    await run_register_event_rules(db, add, business_date, store_ids)
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


async def run_transaction_audit(db, txns, flag):
    """Per-transaction audit errors from stored detail: out of balance, a manual discount with no reason, and the line rules."""
    pos = [
        t
        for t in txns
        if t.source_lineage in (LINEAGE["POS"], LINEAGE["Shopify"])
        and t.reconciliation_status != "Exception"
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
    await run_detail_rules(db, [t for t in pos if t.reconciliation_status != "Exception"], flag)


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
from apps.ingest.shopify.reconciliation import (
    accumulate_shopify_refund_history,
    close_paid_orders,
    current_shopify_refund_amounts,
    flag_unmatched_shopify_refunds,
    group_shopify_refunds,
    load_earliest_shopify_orders,
    load_shopify_order_context,
    reconcile_shopify,
    reconcile_shopify_refund_group,
    shopify_refund_through_dates,
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
    "accumulate_shopify_refund_history",
    "close_paid_orders",
    "current_shopify_refund_amounts",
    "flag_unmatched_shopify_refunds",
    "group_shopify_refunds",
    "load_earliest_shopify_orders",
    "load_shopify_order_context",
    "reconcile_shopify",
    "reconcile_shopify_refund_group",
    "shopify_refund_through_dates",
]
