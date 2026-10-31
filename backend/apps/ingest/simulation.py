"""Synthetic source feeds for local demos, examples, and integration tests."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import random
import uuid

from apps.transactions import detail as transaction_detail


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

    manifests = build_control_manifests(pos, day, run)

    bank, erp = build_settlement_feeds(normal, cardnorm, day, run)
    shopify = build_shopify_feed(day, run)
    return {"POS": pos, "Processor": proc, "POSControl": manifests, "Bank": bank, "ERP": erp, "Shopify": shopify}


def build_control_manifests(pos: list[dict], day: str, run: str) -> list[dict]:
    """Declare valid source rows while keeping the retransmit uncounted."""
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
    return manifests


def build_settlement_feeds(normal: list[tuple[str, Decimal]], cardnorm: list[tuple[str, Decimal]],
                           day: str, run: str) -> tuple[list[dict], list[dict]]:
    """Build bank deposits and ERP postings with representative variances."""
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
    return bank, erp


def build_shopify_feed(day: str, run: str) -> list[dict]:
    """Create one fully refunded and one partially refunded Shopify order."""
    return [
        {"source_record_id": f"SHP-{run}-ORDER-1", "record_type": "Order", "event_time": f"{day}T11:00:00+00:00", "amount": "85.00", "store_id": "STORE-007", "currency": "USD", "payment_reference": f"ORDER-{run}-1", "order_id": f"ORDER-{run}-1", "financial_status": "paid"},
        {"source_record_id": f"SHP-{run}-REFUND-1", "record_type": "Refund", "event_time": f"{day}T12:00:00+00:00", "amount": "85.00", "store_id": "STORE-007", "currency": "USD", "payment_reference": f"ORDER-{run}-1", "order_id": f"ORDER-{run}-1", "refund_id": f"REFUND-{run}-1"},
        {"source_record_id": f"SHP-{run}-ORDER-2", "record_type": "Order", "event_time": f"{day}T13:00:00+00:00", "amount": "120.00", "store_id": "STORE-022", "currency": "USD", "payment_reference": f"ORDER-{run}-2", "order_id": f"ORDER-{run}-2", "financial_status": "paid"},
        {"source_record_id": f"SHP-{run}-REFUND-2", "record_type": "Refund", "event_time": f"{day}T14:00:00+00:00", "amount": "50.00", "store_id": "STORE-022", "currency": "USD", "payment_reference": f"ORDER-{run}-2", "order_id": f"ORDER-{run}-2", "refund_id": f"REFUND-{run}-2"},
    ]
