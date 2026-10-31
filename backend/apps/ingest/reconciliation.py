"""Deterministic transaction matching and sales audit control evaluation."""
from collections import defaultdict, deque
from datetime import datetime
from decimal import Decimal
import json
import uuid

from sqlalchemy import and_, func, or_, select, text, tuple_

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
from models import (
    CanonicalTransaction, Case, Exception as DBException, ReconciliationPolicy,
    SourceRecord, TransactionDiscount, TransactionTender,
)


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
    amounts = await effective_amounts(db, txns)
    pos = sorted([t for t in txns if t.source_lineage == LINEAGE["POS"]], key=lambda t: t.event_timestamp)
    proc = [t for t in txns if t.source_lineage == LINEAGE["Processor"]]
    policy = (await db.execute(select(ReconciliationPolicy).where(ReconciliationPolicy.active == True)
                               .order_by(ReconciliationPolicy.updated_at.desc(), ReconciliationPolicy.id.desc())
                               .limit(1))).scalar_one_or_none()  # noqa: E712
    amount_tolerance = policy.amount_tolerance if policy else Decimal("0")
    tolerance_currency = policy.amount_tolerance_currency if policy else "USD"
    settlement_day_tolerance = policy.settlement_day_tolerance if policy else 0
    # A sale part-paid by voucher or cash is captured by the processor for the card part only.
    tenders = defaultdict(list)
    pos_ids = [t.id for t in pos]
    for i in range(0, len(pos_ids), 500):
        for td in (await db.execute(select(TransactionTender).where(TransactionTender.transaction_id.in_(pos_ids[i:i + 500])))).scalars().all():
            tenders[td.transaction_id].append({"tender_type": td.tender_type, "amount": td.amount})
    proc_idx = defaultdict(deque)
    for t in proc:
        if t.payment_reference:
            proc_idx[(t.store_id, t.currency, t.payment_reference, t.transaction_type)].append(t)

    findings, matched, used = [], 0, set()

    def add(etype, store, bdate, amount, currency):
        findings.append((etype, store, bdate, amount, [], currency))

    def flag(etype, amount, *group):
        findings.append((etype, group[0].store_id, group[0].business_date, amount, [t.id for t in group], group[0].currency))
        for t in group:
            t.reconciliation_status, t.disposition = "Exception", "Awaiting"

    seen, dupes = set(), set()
    for t in pos:
        if t.transaction_type != "Sale" or not t.payment_reference:
            continue
        key = (t.store_id, t.business_date, t.register_id, t.currency, amounts[t.id], t.payment_reference)
        if key in seen:
            dupes.add(t.id)
            flag("DUPLICATE_SALES", abs(amounts[t.id]), t)
        seen.add(key)

    for t in pos:
        if t.id in dupes:
            continue
        want = "Capture" if t.transaction_type == "Sale" else "Refund"
        expected = transaction_detail.card_amount(tenders.get(t.id, []), amounts[t.id])
        if tenders.get(t.id) and expected == 0:      # paid entirely without a card, so there is no processor leg to match
            t.reconciliation_status, t.disposition = "Matched", "Closed"
            matched += 1
            continue
        candidates = proc_idx.get((t.store_id, t.currency, t.payment_reference, want), ()) if t.payment_reference else ()
        if len(candidates) != 1:
            flag("MISSING_REFUND" if t.transaction_type == "Return" else "UNMATCHED_SALE", abs(amounts[t.id]), t)
            continue
        p = candidates.popleft()
        used.add(p.id)
        if abs(amounts[p.id] - expected) > (amount_tolerance if t.currency == tolerance_currency else Decimal("0")):
            flag("AMOUNT_MISMATCH", abs(amounts[p.id] - expected), t, p)
        elif (datetime.fromisoformat(p.settlement_date).date() - datetime.fromisoformat(t.business_date).date()).days > settlement_day_tolerance:
            flag("TIMING_DIFFERENCE", abs(amounts[t.id]), t, p)
        else:
            for x in (t, p):
                x.reconciliation_status, x.disposition = "Matched", "Closed"
            matched += 1

    for p in proc:
        if p.id not in used:
            flag("ORPHAN_PAYMENT", abs(amounts[p.id]), p)

    await reconcile_shopify(db, txns, flag)
    await run_transaction_audit(db, txns, flag)
    await run_controls(db, business_date, add, policy, store_ids=store_ids)
    await run_settlement_controls(db, txns, add, amount_tolerance, tolerance_currency)

    groups = defaultdict(list)
    for f in findings:
        groups[(f[0], f[1], f[2], f[5])].append(f)

    counts, cases = defaultdict(int), 0
    for (etype, store, bdate, currency), items in groups.items():
        od = ontology.get(etype)
        amount = sum((i[3] for i in items), Decimal("0"))
        exposure = Decimal("0") if od["exposure"] == "zero" else amount
        cid = str(uuid.uuid4())
        db.add(Case(
            id=cid, case_number=generate_case_number(bdate), status="Open",
            case_type=od["label"], business_date=bdate, store_id=store, currency=currency, total_exception_amount=amount,
            total_exposure=exposure, investigation_status="Pending", evidence_completeness=0,
            priority=(prio := "Critical" if amount > 3000 else "High" if amount > 1000 else "Normal"),
            sla_due_at=sla_due(prio)))
        db.add(DBException(
            id=str(uuid.uuid4()), case_id=cid, exception_type=etype, exception_family=od["family"],
            source_system="Reconciliation", detection_origin="Reconciliation", detection_rule_id=f"RECON-{etype}",
            exception_amount=amount, estimated_exposure=exposure, severity="High" if amount > 1000 else "Medium",
            confidence="High", close_impact="Yes" if amount else "No"))
        audit(db, "CASE_CREATED", cid, "Case", cid,
              f"Reconciliation raised {od['label']} ({len(items)} item(s), {amount})",
              actor="reconciliation-engine", after={"transaction_ids": [i for it in items for i in it[4]]})
        counts[etype] += len(items)
        cases += 1

    return {"transactions_examined": len(txns), "matched_pairs": matched,
            "exceptions": dict(counts), "cases_created": cases}


async def reconcile_shopify(db, txns, flag):
    """Match Shopify refunds to their order (by store, currency, order id) and compare totals per order."""
    shopify = [t for t in txns if t.source_lineage == LINEAGE["Shopify"]]
    current_amounts = await effective_amounts(db, shopify)
    refunds = [t for t in shopify if t.transaction_type == "Refund"]
    await close_paid_orders(db, [t for t in shopify if t.transaction_type == "Order"])

    by_order = defaultdict(list)
    for r in refunds:
        by_order[(r.store_id, r.currency, r.payment_reference)].append(r)

    group_amounts = {
        key: sum((abs(current_amounts[refund.id]) for refund in group), Decimal("0"))
        for key, group in by_order.items()
    }
    for key, group in by_order.items():
        if not key[2]:
            flag("SHOPIFY_UNMATCHED_REFUND", group_amounts[key], *group)

    through_dates = {
        key: max(refund.business_date for refund in group)
        for key, group in by_order.items()
        if key[2]
    }
    order_keys = list(through_dates)
    orders_by_key = {}
    historical_refund_totals: dict[tuple[str, str, str], Decimal] = defaultdict(Decimal)
    for offset in range(0, len(order_keys), SHOPIFY_ORDER_KEY_BATCH_SIZE):
        key_batch = order_keys[offset:offset + SHOPIFY_ORDER_KEY_BATCH_SIZE]
        key_filter = tuple_(
            CanonicalTransaction.store_id,
            CanonicalTransaction.currency,
            CanonicalTransaction.payment_reference,
        ).in_(key_batch)

        # Preserve earliest-event selection while fetching keys in a batch. IDs are
        # only a deterministic tie-breaker; their lexical order is not chronological.
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
        orders = (await db.execute(
            select(CanonicalTransaction).join(
                ranked_orders, CanonicalTransaction.id == ranked_orders.c.id
            ).where(ranked_orders.c.order_rank == 1).order_by(CanonicalTransaction.id).with_for_update()
            .execution_options(populate_existing=True)
        )).scalars().all()
        orders_by_key.update({
            (order.store_id, order.currency, order.payment_reference): order
            for order in orders
        })

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
            historical_refund_totals,
            max_groups=MAX_RECONCILIATION_TRANSACTIONS,
        )

    order_amounts = await effective_amounts(db, list(orders_by_key.values()))
    for key, group in by_order.items():
        if not key[2]:
            continue
        refunded = group_amounts[key]
        order = orders_by_key.get(key)
        if not order:
            flag("SHOPIFY_UNMATCHED_REFUND", refunded, *group)
            continue
        if order.reconciliation_status == "Manually Matched":
            flag("SHOPIFY_REFUND_MANUAL_REVIEW", refunded, *group)
            continue

        # Include prior refunds, but never later business days when replaying a historical day.
        refunded = abs(historical_refund_totals[key])
        order_amount = abs(order_amounts[order.id])
        if refunded > order_amount:
            flag("SHOPIFY_OVERAGE_REFUND", refunded - order_amount, *group, order)
        elif refunded < order_amount:
            flag("SHOPIFY_PARTIAL_REFUND", refunded, *group, order)
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
        scopes = {(row.store_id, row.currency, row.business_date) for row in referenced_banks}
        expected, actual = defaultdict(Decimal), defaultdict(Decimal)
        ordered_scopes = sorted(scopes)
        # A scope uses three bind parameters; keep each query safely below SQLite's
        # traditional 999-variable limit and avoid the cross-product from separate IN lists.
        for offset in range(0, len(ordered_scopes), COMPOSITE_SCOPE_BATCH_SIZE):
            scope_batch = ordered_scopes[offset:offset + COMPOSITE_SCOPE_BATCH_SIZE]
            expected_query = select(CanonicalTransaction).where(
                CanonicalTransaction.source_lineage == LINEAGE["Processor"],
                CanonicalTransaction.reconciliation_status == "Matched",
                tuple_(CanonicalTransaction.store_id, CanonicalTransaction.currency,
                       CanonicalTransaction.settlement_date).in_(scope_batch),
                CanonicalTransaction.settlement_reference.is_not(None))
            await accumulate_reconciliation_amounts(
                db,
                expected_query,
                lambda row: (row.store_id, row.currency, row.settlement_date, row.settlement_reference),
                expected,
                max_groups=MAX_SETTLEMENT_GROUPS,
            )

            bank_query = select(CanonicalTransaction).where(
                CanonicalTransaction.source_lineage == LINEAGE["Bank"],
                tuple_(CanonicalTransaction.store_id, CanonicalTransaction.currency,
                       CanonicalTransaction.business_date).in_(scope_batch),
                CanonicalTransaction.settlement_reference.is_not(None))
            await accumulate_reconciliation_amounts(
                db,
                bank_query,
                lambda row: (row.store_id, row.currency, row.business_date, row.settlement_reference),
                actual,
                max_groups=MAX_SETTLEMENT_GROUPS,
            )
        for key in set(expected) | set(actual):
            variance = expected.get(key, Decimal("0")) - actual.get(key, Decimal("0"))
            if abs(variance) > (amount_tolerance if key[1] == tolerance_currency else Decimal("0")):
                add("MISSING_DEPOSIT" if key not in actual else "BANK_VARIANCE", key[0], key[2], abs(variance), key[1])
        # A referenced deposit is governed by its batch comparison, not the coarser daily total.
        for row in referenced_banks:
            key = (row.store_id, row.currency, row.business_date, row.settlement_reference)
            tolerance = amount_tolerance if row.currency == tolerance_currency else Decimal("0")
            ok = abs(expected.get(key, Decimal("0")) - actual.get(key, Decimal("0"))) <= tolerance
            row.reconciliation_status, row.disposition = ("Matched", "Closed") if ok else ("Exception", "Awaiting")
    for feed_key, var, missing, basis in (("Bank", "BANK_VARIANCE", "MISSING_DEPOSIT", "Processor"),
                                          ("ERP", "GL_VARIANCE", "MISSING_GL_POSTING", "POS")):
        feed = [t for t in txns if t.source_lineage == LINEAGE[feed_key] and (feed_key != "Bank" or not t.settlement_reference)]
        if not feed:
            continue
        expected, got, by_store = defaultdict(Decimal), defaultdict(Decimal), defaultdict(list)
        feed_amounts = await effective_amounts(db, feed)
        scopes = sorted({(row.store_id, row.currency, row.business_date) for row in feed})
        for row in feed:
            key = (row.store_id, row.currency, row.business_date)
            got[key] += feed_amounts[row.id]
            by_store[key].append(row)

        # Only matched source transactions for scopes represented by this feed can
        # affect its reconciliation. Read them in bounded batches across all dates.
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

        for key in expected.keys() | got.keys():
            store, currency, business_date = key
            expected_amount, feed_amount = expected.get(key, Decimal("0")), got.get(key)
            if feed_amount is None:
                if expected_amount != 0:
                    add(missing, store, business_date, abs(expected_amount), currency)
                continue
            tolerance = amount_tolerance if currency == tolerance_currency else Decimal("0")
            balanced = abs(expected_amount - feed_amount) <= tolerance
            if not balanced:
                add(var, store, business_date, abs(expected_amount - feed_amount), currency)
            for row in by_store[key]:
                row.reconciliation_status, row.disposition = ("Matched", "Closed") if balanced else ("Exception", "Awaiting")


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
