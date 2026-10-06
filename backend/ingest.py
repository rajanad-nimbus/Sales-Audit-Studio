"""Ingestion (archive, validate, normalize) and deterministic reconciliation."""
import hashlib
import json
import random
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import select

import ontology
import shopify_ingest
import transaction_detail
from models import (Case, CanonicalTransaction, Exception as DBException, ReconciliationPolicy, SourceRecord, TransactionDiscount,
                    TransactionLine, TransactionTax, TransactionTender, utcnow)
from nimbus import audit, sla_due

REQUIRED = ["source_record_id", "event_time", "record_type", "amount", "store_id"]
SIGN = {"Sale": 1, "Capture": 1, "Return": -1, "Refund": -1, "Deposit": 1, "Posting": 1, "Order": 1}
VALID_TYPES = {"POS": {"Sale", "Return"}, "Processor": {"Capture", "Refund"}, "Bank": {"Deposit"},
               "ERP": {"Posting"}, "Shopify": {"Order", "Refund"}, "POSControl": set()}
LINEAGE = {"POS": "pos-map-v1", "Processor": "processor-map-v1", "Bank": "bank-map-v1", "ERP": "erp-map-v1", "Shopify": "shopify-map-v1"}


def parse(source: str, rec: dict) -> dict:
    if source not in VALID_TYPES:
        raise ValueError(f"unknown source_system {source}")
    if source == "POSControl":
        need = ["source_record_id", "business_date", "store_id", "declared_count", "declared_total"]
        missing = [k for k in need if rec.get(k) in (None, "")]
        if missing:
            raise ValueError(f"missing fields: {', '.join(missing)}")
        try:
            return {"manifest": True, "count": int(rec["declared_count"]),
                    "total": Decimal(str(rec["declared_total"])).quantize(Decimal("0.01")),
                    "ts": datetime.fromisoformat(str(rec["business_date"])).replace(tzinfo=timezone.utc)}
        except (ValueError, InvalidOperation):
            raise ValueError("invalid manifest values")
    if source == "Shopify":
        return shopify_ingest.parse(rec)
    missing = [k for k in REQUIRED if rec.get(k) in (None, "")]
    if missing:
        raise ValueError(f"missing fields: {', '.join(missing)}")
    if rec["record_type"] not in VALID_TYPES[source]:
        raise ValueError(f"invalid record_type {rec['record_type']} for {source}")
    try:
        amount = Decimal(str(rec["amount"])).quantize(Decimal("0.01"))
    except InvalidOperation:
        raise ValueError("amount is not a decimal")
    if amount <= 0:
        raise ValueError("amount must be positive")
    try:
        ts = datetime.fromisoformat(str(rec["event_time"]).replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("event_time is not ISO-8601")
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    try:
        detail = transaction_detail.extract(rec, SIGN[rec["record_type"]])
    except ValueError as e:
        raise ValueError(f"invalid transaction detail: {e}")
    return {"amount": amount, "ts": ts, "detail": detail}


def store_detail(db, tx: CanonicalTransaction, detail: dict | None) -> None:
    """Persist the lines, tax, discounts and tenders of a transaction and record whether they add up to its amount."""
    if not detail:
        return
    for ln in detail["lines"]:
        db.add(TransactionLine(id=str(uuid.uuid4()), transaction_id=tx.id, **ln))
    for t in detail["taxes"]:
        db.add(TransactionTax(id=str(uuid.uuid4()), transaction_id=tx.id, **t))
    for d in detail["discounts"]:
        db.add(TransactionDiscount(id=str(uuid.uuid4()), transaction_id=tx.id, **d))
    for t in detail["tenders"]:
        db.add(TransactionTender(id=str(uuid.uuid4()), transaction_id=tx.id, **t))
    tx.original_reference, tx.return_reason = detail["original_reference"], detail["return_reason"]
    tx.detail_status = transaction_detail.check(detail, tx.signed_amount)["status"]


async def link_returns(db) -> int:
    """Point each return at the sale it reverses, once that sale has arrived. Matches the sale's POS record id or payment reference."""
    pending = (await db.execute(select(CanonicalTransaction).where(
        CanonicalTransaction.transaction_type == "Return", CanonicalTransaction.original_reference.is_not(None),
        CanonicalTransaction.original_transaction_id.is_(None)))).scalars().all()
    linked = 0
    for r in pending:
        sale = (await db.execute(
            select(CanonicalTransaction).outerjoin(SourceRecord, CanonicalTransaction.source_record_id == SourceRecord.id)
            .where(CanonicalTransaction.transaction_type == "Sale", CanonicalTransaction.store_id == r.store_id,
                   (SourceRecord.source_record_id == r.original_reference) | (CanonicalTransaction.payment_reference == r.original_reference))
            .order_by(CanonicalTransaction.event_timestamp).limit(1))).scalar_one_or_none()
        if sale:
            r.original_transaction_id = sale.id
            linked += 1
    return linked


async def ingest(db, source: str, delivery_id: str, records: list[dict]) -> dict:
    stats = {"received": 0, "archived": 0, "duplicates_skipped": 0, "quarantined": 0, "canonical_created": 0}
    for rec in records:
        stats["received"] += 1
        payload = json.dumps(rec, sort_keys=True, default=str).encode()
        hashed = shopify_ingest.identity_payload(rec) if source == "Shopify" else rec
        digest = hashlib.sha256(json.dumps(hashed, sort_keys=True, default=str).encode()).hexdigest()
        srid = str(rec.get("source_record_id") or f"unknown-{digest[:12]}")
        prior = (await db.execute(select(SourceRecord.payload_hash).where(
            SourceRecord.source_system == source, SourceRecord.source_record_id == srid))).scalars().all()
        if digest in prior:
            stats["duplicates_skipped"] += 1
            continue

        status, reason, parsed = "Archived", None, None
        if prior:
            status, reason = "Quarantined", "Conflicting resubmission: same record id, different payload"
        else:
            try:
                parsed = parse(source, rec)
            except ValueError as e:
                status, reason = "Quarantined", str(e)

        sr = SourceRecord(
            id=str(uuid.uuid4()), source_system=source, source_version="1.0", source_record_id=srid,
            delivery_id=delivery_id, payload=payload, payload_hash=digest, received_at=utcnow(),
            source_event_time=parsed["ts"] if parsed else utcnow(), status=status, quarantine_reason=reason)
        db.add(sr)
        await db.flush()
        if status == "Quarantined":
            stats["quarantined"] += 1
            continue
        stats["archived"] += 1
        if parsed.get("manifest"):
            continue

        ts, bd = parsed["ts"], parsed["ts"].date().isoformat()
        tx = CanonicalTransaction(
            id=str(uuid.uuid4()), business_date=bd, transaction_date=ts, event_timestamp=ts,
            processing_timestamp=utcnow(), settlement_date=str(rec.get("settled_on") or bd),
            transaction_type=rec["record_type"], source_lineage=LINEAGE[source],
            currency=rec.get("currency", "USD"), signed_amount=SIGN[rec["record_type"]] * parsed["amount"],
            store_id=rec["store_id"], register_id=rec.get("register_id"), channel_id=rec.get("channel_id", "store"),
            tender_type=rec.get("tender_type"), payment_reference=rec.get("payment_reference"),
            settlement_reference=rec.get("settlement_reference"),
            source_record_id=sr.id, reconciliation_status="Unmatched", disposition="Open")
        db.add(tx)
        await db.flush()
        store_detail(db, tx, parsed.get("detail"))
        sr.status, sr.extracted_at = "Processed", utcnow()
        stats["canonical_created"] += 1
    await db.flush()
    stats["returns_linked"] = await link_returns(db)
    return stats


async def reconcile(db, business_date: str | None = None) -> dict:
    q = select(CanonicalTransaction).where(CanonicalTransaction.reconciliation_status == "Unmatched")
    if business_date:
        q = q.where(CanonicalTransaction.business_date == business_date)
    txns = (await db.execute(q)).scalars().all()
    pos = sorted([t for t in txns if t.source_lineage == LINEAGE["POS"]], key=lambda t: t.event_timestamp)
    proc = [t for t in txns if t.source_lineage == LINEAGE["Processor"]]
    policy = (await db.execute(select(ReconciliationPolicy).where(ReconciliationPolicy.active == True))).scalars().first()  # noqa: E712
    amount_tolerance = policy.amount_tolerance if policy else Decimal("0")
    settlement_day_tolerance = policy.settlement_day_tolerance if policy else 0
    # A sale part-paid by voucher or cash is captured by the processor for the card part only.
    tenders = defaultdict(list)
    pos_ids = [t.id for t in pos]
    for i in range(0, len(pos_ids), 500):
        for td in (await db.execute(select(TransactionTender).where(TransactionTender.transaction_id.in_(pos_ids[i:i + 500])))).scalars().all():
            tenders[td.transaction_id].append({"tender_type": td.tender_type, "amount": td.amount})
    proc_idx = defaultdict(list)
    for t in proc:
        if t.payment_reference:
            proc_idx[(t.store_id, t.currency, t.payment_reference, t.transaction_type)].append(t)

    findings, matched, used = [], 0, set()

    def add(etype, store, bdate, amount):
        findings.append((etype, store, bdate, amount, []))

    def flag(etype, amount, *group):
        findings.append((etype, group[0].store_id, group[0].business_date, amount, [t.id for t in group]))
        for t in group:
            t.reconciliation_status, t.disposition = "Exception", "Awaiting"

    seen, dupes = set(), set()
    for t in pos:
        if t.transaction_type != "Sale":
            continue
        key = (t.store_id, t.register_id, t.signed_amount, t.payment_reference)
        if key in seen:
            dupes.add(t.id)
            flag("DUPLICATE_SALES", abs(t.signed_amount), t)
        seen.add(key)

    for t in pos:
        if t.id in dupes:
            continue
        want = "Capture" if t.transaction_type == "Sale" else "Refund"
        expected = transaction_detail.card_amount(tenders.get(t.id, []), t.signed_amount)
        if tenders.get(t.id) and expected == 0:      # paid entirely without a card, so there is no processor leg to match
            t.reconciliation_status, t.disposition = "Matched", "Closed"
            matched += 1
            continue
        candidates = proc_idx.get((t.store_id, t.currency, t.payment_reference, want), []) if t.payment_reference else []
        candidates = [candidate for candidate in candidates if candidate.id not in used]
        if len(candidates) != 1:
            flag("MISSING_REFUND" if t.transaction_type == "Return" else "UNMATCHED_SALE", abs(t.signed_amount), t)
            continue
        p = candidates[0]
        used.add(p.id)
        if abs(p.signed_amount - expected) > amount_tolerance:
            flag("AMOUNT_MISMATCH", abs(p.signed_amount - expected), t, p)
        elif (datetime.fromisoformat(p.settlement_date).date() - datetime.fromisoformat(t.business_date).date()).days > settlement_day_tolerance:
            flag("TIMING_DIFFERENCE", abs(t.signed_amount), t, p)
        else:
            for x in (t, p):
                x.reconciliation_status, x.disposition = "Matched", "Closed"
            matched += 1

    for p in proc:
        if p.id not in used:
            flag("ORPHAN_PAYMENT", abs(p.signed_amount), p)

    await reconcile_shopify(db, txns, flag)
    await run_controls(db, business_date, add)
    await run_settlement_controls(db, txns, add, amount_tolerance)

    groups = defaultdict(list)
    for f in findings:
        groups[(f[0], f[1], f[2])].append(f)

    counts, cases = defaultdict(int), 0
    for (etype, store, bdate), items in groups.items():
        od = ontology.get(etype)
        amount = sum((i[3] for i in items), Decimal("0"))
        exposure = Decimal("0") if od["exposure"] == "zero" else amount
        cid = str(uuid.uuid4())
        db.add(Case(
            id=cid, case_number=f"ZA-{bdate.replace('-', '')}-{uuid.uuid4().hex[:4].upper()}", status="Open",
            case_type=od["label"], business_date=bdate, store_id=store, total_exception_amount=amount,
            total_exposure=exposure, investigation_status="Pending", evidence_completeness=0,
            priority=(prio := "Critical" if amount > 3000 else "High" if amount > 1000 else "Normal"),
            sla_due_at=sla_due(prio)))
        await db.flush()
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
    refunds = [t for t in shopify if t.transaction_type == "Refund"]
    await close_paid_orders(db, [t for t in shopify if t.transaction_type == "Order"])

    by_order = defaultdict(list)
    for r in refunds:
        by_order[(r.store_id, r.currency, r.payment_reference)].append(r)

    for (store, currency, ref), group in by_order.items():
        # The order may belong to an earlier business date, so look beyond today's unmatched set.
        order = (await db.execute(select(CanonicalTransaction).where(
            CanonicalTransaction.source_lineage == LINEAGE["Shopify"], CanonicalTransaction.transaction_type == "Order",
            CanonicalTransaction.store_id == store, CanonicalTransaction.currency == currency,
            CanonicalTransaction.payment_reference == ref))).scalars().first()
        refunded = sum((abs(r.signed_amount) for r in group), Decimal("0"))

        if not order:
            flag("SHOPIFY_UNMATCHED_REFUND", refunded, *group)
            continue
        order_amount = abs(order.signed_amount)
        if refunded > order_amount:
            flag("SHOPIFY_OVERAGE_REFUND", refunded - order_amount, *group, order)
        elif refunded < order_amount:
            flag("SHOPIFY_PARTIAL_REFUND", refunded, *group, order)
        else:
            for t in (*group, order):
                t.reconciliation_status, t.disposition = "Matched", "Closed"


async def close_paid_orders(db, orders):
    """Close paid orders that have no refund on file; there is nothing further to reconcile.

    Pending, authorized or voided orders stay open. A refund that arrives later still finds the
    order (matching ignores its status) and reopens it if the totals disagree.
    """
    orders = [o for o in orders if o.reconciliation_status == "Unmatched"]
    if not orders:
        return
    refs = {o.payment_reference for o in orders}
    refunded = set((await db.execute(select(CanonicalTransaction.payment_reference).where(
        CanonicalTransaction.source_lineage == LINEAGE["Shopify"], CanonicalTransaction.transaction_type == "Refund",
        CanonicalTransaction.payment_reference.in_(refs)))).scalars().all())
    srs = {sr.id: sr for sr in (await db.execute(select(SourceRecord).where(
        SourceRecord.id.in_([o.source_record_id for o in orders])))).scalars().all()}
    for o in orders:
        sr = srs.get(o.source_record_id)
        if o.payment_reference in refunded or sr is None:
            continue
        if json.loads(sr.payload).get("financial_status") == "paid":
            o.reconciliation_status, o.disposition = "Matched", "Closed"


async def run_controls(db, business_date, add):
    """Completeness and balancing: compare POS control manifests with ingested POS transactions."""
    q = select(SourceRecord).where(SourceRecord.source_system == "POSControl", SourceRecord.status == "Archived")
    for m in (await db.execute(q)).scalars().all():
        d = json.loads(m.payload)
        store, bd = d["store_id"], str(d["business_date"])[:10]
        if business_date and bd != business_date:
            continue
        rows = (await db.execute(select(CanonicalTransaction).where(
            CanonicalTransaction.source_lineage == LINEAGE["POS"], CanonicalTransaction.store_id == store,
            CanonicalTransaction.business_date == bd))).scalars().all()
        count, total = len(rows), sum((r.signed_amount for r in rows), Decimal("0"))
        dcount, dtotal = int(d["declared_count"]), Decimal(str(d["declared_total"]))
        if count < dcount:
            add("INCOMPLETE_FEED", store, bd, abs(dtotal - total))
        elif count != dcount or total != dtotal:
            add("BALANCING_VARIANCE", store, bd, abs(total - dtotal))
        m.status = "Processed"


async def run_settlement_controls(db, txns, add, amount_tolerance=Decimal("0")):
    """Reconcile settlement batches when references exist, otherwise retain store/day controls."""
    bank = [t for t in txns if t.source_lineage == LINEAGE["Bank"]]
    referenced_banks = [t for t in bank if t.settlement_reference]
    if referenced_banks:
        expected_rows = (await db.execute(select(CanonicalTransaction).where(
            CanonicalTransaction.source_lineage == LINEAGE["Processor"],
            CanonicalTransaction.reconciliation_status == "Matched",
            CanonicalTransaction.settlement_reference.is_not(None)))).scalars().all()
        expected, actual, batch_store, batch_date = defaultdict(Decimal), defaultdict(Decimal), {}, {}
        for row in expected_rows:
            key = (row.store_id, row.settlement_reference)
            expected[key] += row.signed_amount; batch_store[key], batch_date[key] = row.store_id, row.settlement_date
        for row in referenced_banks:
            key = (row.store_id, row.settlement_reference)
            actual[key] += row.signed_amount; batch_store[key], batch_date[key] = row.store_id, row.business_date
        for key in set(expected) | set(actual):
            variance = expected.get(key, Decimal("0")) - actual.get(key, Decimal("0"))
            if abs(variance) > amount_tolerance:
                add("MISSING_DEPOSIT" if actual.get(key, Decimal("0")) == 0 else "BANK_VARIANCE", batch_store[key], batch_date[key], abs(variance))
        # A referenced deposit is governed by its batch comparison, not the coarser daily total.
        for row in referenced_banks:
            key = (row.store_id, row.settlement_reference)
            row.reconciliation_status, row.disposition = ("Matched", "Closed") if expected.get(key, Decimal("0")) == actual.get(key, Decimal("0")) else ("Exception", "Awaiting")
    for feed_key, var, missing, basis in (("Bank", "BANK_VARIANCE", "MISSING_DEPOSIT", "Processor"),
                                          ("ERP", "GL_VARIANCE", "MISSING_GL_POSTING", "POS")):
        feed = [t for t in txns if t.source_lineage == LINEAGE[feed_key] and (feed_key != "Bank" or not t.settlement_reference)]
        for bd in {t.business_date for t in feed}:
            rows = (await db.execute(select(CanonicalTransaction).where(
                CanonicalTransaction.source_lineage == LINEAGE[basis], CanonicalTransaction.business_date == bd,
                CanonicalTransaction.reconciliation_status == "Matched"))).scalars().all()
            expected, got, by_store = defaultdict(Decimal), defaultdict(Decimal), defaultdict(list)
            for r in rows:
                expected[r.store_id] += r.signed_amount
            for t in feed:
                if t.business_date == bd:
                    got[t.store_id] += t.signed_amount
                    by_store[t.store_id].append(t)
            for store in set(expected) | set(got):
                e, g = expected.get(store, Decimal("0")), got.get(store)
                if g is None:
                    if e != 0:
                        add(missing, store, bd, abs(e))
                    continue
                ok = abs(e - g) <= amount_tolerance
                if not ok:
                    add(var, store, bd, abs(e - g))
                for t in by_store[store]:
                    t.reconciliation_status, t.disposition = ("Matched", "Closed") if ok else ("Exception", "Awaiting")


def simulate_feed(day: str | None = None) -> dict[str, list[dict]]:
    day = day or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    nxt = (datetime.fromisoformat(day) + timedelta(days=1)).strftime("%Y-%m-%d")
    run = uuid.uuid4().hex[:6]
    pos, proc, normal = [], [], []

    def mk(i, amount, typ_pos, typ_proc, store, ref, settled=None, hour=None, with_proc=True, voucher=False, original=None, detail=None):
        t = f"{day}T{hour if hour is not None else random.randint(9, 20):02d}:{random.randint(0, 59):02d}:00+00:00"
        batch = f"SET-{run}-{store}"
        base = {"event_time": t, "store_id": store, "currency": "USD", "payment_reference": ref, "settlement_reference": batch}
        # Items, VAT, discounts (promotion, coupon, voucher) and tenders that add up to the amount.
        detail = detail or transaction_detail.synthesize(amount, random, original_reference=original, voucher_tender=voucher)
        card = sum((Decimal(x["amount"]) for x in detail["tenders"] if x["tender_type"] == "Card"), Decimal("0"))
        pos.append({**base, "source_record_id": f"POS-{run}-{i}", "record_type": typ_pos, "amount": str(amount),
                    "register_id": f"R{random.randint(1, 4)}", "tender_type": "Card", **detail})
        if with_proc:   # the processor only sees the card part of a split payment
            proc.append({**base, "source_record_id": f"PRC-{run}-{i}", "record_type": typ_proc,
                         "amount": str(card), "settled_on": settled or day})
        return card

    stores = [f"STORE-{n:03d}" for n in (7, 14, 22, 31)]
    cardnorm = []   # card money by store: what the bank deposit should hold (cash and vouchers do not reach the processor)
    for i in range(24):
        store, amt = random.choice(stores), Decimal(random.randint(1500, 90000)) / 100
        card = mk(i, amt, "Sale", "Capture", store, f"PAY-{run}-{i}", voucher=(i % 6 == 3))
        normal.append((store, amt))
        cardnorm.append((store, card))
    mk(100, Decimal("550.00"), "Sale", "Capture", "STORE-014", f"PAY-{run}-T", settled=nxt, hour=23)
    mk(101, Decimal("3420.00"), "Return", "Refund", "STORE-007", f"PAY-{run}-MR", with_proc=False, original=f"POS-{run}-9999")
    mk(102, Decimal("128.00"), "Sale", "Capture", "STORE-022", f"PAY-{run}-D")
    normal.append(("STORE-022", Decimal("128.00")))
    cardnorm.append(("STORE-022", Decimal("128.00")))
    pos.append({**pos[-1], "source_record_id": f"POS-{run}-102b"})
    # Returns built from the original sale's own lines: one ordinary, and one returning more than was sold.
    for n, (sale_idx, extra) in enumerate(((0, 0), (1, 1))):
        sale = next(r for r in pos if r["source_record_id"] == f"POS-{run}-{sale_idx}")
        total, ret_detail = transaction_detail.synthesize_return(sale, random, extra_qty=extra, reason="Wrong size" if not extra else "Customer claims two were bought")
        mk(110 + n, total, "Return", "Refund", sale["store_id"], f"PAY-{run}-R{n}", detail=ret_detail)
        normal.append((sale["store_id"], -total))
        cardnorm.append((sale["store_id"], -total))
    mk(103, Decimal("210.00"), "Sale", "Capture", "STORE-031", f"PAY-{run}-AM")
    proc[-1]["amount"] = "200.00"
    proc.append({"source_record_id": f"PRC-{run}-ORPH", "event_time": f"{day}T15:00:00+00:00", "record_type": "Capture",
                 "amount": "75.25", "store_id": "STORE-014", "payment_reference": f"PAY-{run}-ORPH", "settled_on": day})
    pos.append({"source_record_id": f"POS-{run}-BAD", "event_time": f"{day}T10:00:00+00:00", "record_type": "Sale",
                "store_id": "STORE-007"})
    pos.append(dict(pos[0]))

    # Control manifests declare what each store really sent (the retransmitted 102b is not declared;
    # the malformed BAD record is, so STORE-007 shows an incomplete feed).
    uniq = {}
    for r in pos:
        if not r["source_record_id"].endswith("102b"):
            uniq.setdefault(r["source_record_id"], r)
    declared = defaultdict(lambda: [0, Decimal("0")])
    for r in uniq.values():
        d = declared[r["store_id"]]
        d[0] += 1
        if "amount" in r:
            d[1] += (1 if r["record_type"] == "Sale" else -1) * Decimal(r["amount"])
    manifests = [{"source_record_id": f"MAN-{run}-{s}", "business_date": day, "store_id": s,
                  "declared_count": c, "declared_total": str(t)} for s, (c, t) in declared.items()]

    net = defaultdict(Decimal)
    for store, amt in normal:
        net[store] += amt
    cardnet = defaultdict(Decimal)
    for store, amt in cardnorm:
        cardnet[store] += amt
    bank = [{"source_record_id": f"BNK-{run}-{s}", "event_time": f"{day}T22:00:00+00:00", "record_type": "Deposit",
             "amount": str(v - (Decimal("12.50") if s == "STORE-014" and v > 20 else 0)), "store_id": s,
             "settlement_reference": f"SET-{run}-{s}"}
            for s, v in cardnet.items() if v > 0]
    erp = [{"source_record_id": f"ERP-{run}-{s}", "event_time": f"{day}T23:00:00+00:00", "record_type": "Posting",
            "amount": str(v + (Decimal("5.00") if s == "STORE-031" else 0)), "store_id": s}
           for s, v in net.items() if v > 0 and s != "STORE-022"]
    # Omnichannel simulation: one completed refund and one deliberately partial refund.
    shopify = [
        {"source_record_id": f"SHP-{run}-ORDER-1", "record_type": "Order", "event_time": f"{day}T11:00:00+00:00", "amount": "85.00", "store_id": "STORE-007", "currency": "USD", "payment_reference": f"ORDER-{run}-1", "order_id": f"ORDER-{run}-1", "financial_status": "paid"},
        {"source_record_id": f"SHP-{run}-REFUND-1", "record_type": "Refund", "event_time": f"{day}T12:00:00+00:00", "amount": "85.00", "store_id": "STORE-007", "currency": "USD", "payment_reference": f"ORDER-{run}-1", "order_id": f"ORDER-{run}-1", "refund_id": f"REFUND-{run}-1"},
        {"source_record_id": f"SHP-{run}-ORDER-2", "record_type": "Order", "event_time": f"{day}T13:00:00+00:00", "amount": "120.00", "store_id": "STORE-022", "currency": "USD", "payment_reference": f"ORDER-{run}-2", "order_id": f"ORDER-{run}-2", "financial_status": "paid"},
        {"source_record_id": f"SHP-{run}-REFUND-2", "record_type": "Refund", "event_time": f"{day}T14:00:00+00:00", "amount": "50.00", "store_id": "STORE-022", "currency": "USD", "payment_reference": f"ORDER-{run}-2", "order_id": f"ORDER-{run}-2", "refund_id": f"REFUND-{run}-2"},
    ]
    return {"POS": pos, "Processor": proc, "POSControl": manifests, "Bank": bank, "ERP": erp, "Shopify": shopify}
