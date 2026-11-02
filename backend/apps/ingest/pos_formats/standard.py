"""The standard POS transaction log: one shape for every POS system's log file.

Each adapter (RTLOG today) turns a vendor file into `StandardLog`. From there `ingest_records` maps the sales and
returns to the POS ingest contract, and `register_events` are the till and drawer activity that has no sale behind it.
"""

from dataclasses import dataclass, field
from decimal import Decimal

# Standard transaction types, and what each one is.
SALE, RETURN, EXCHANGE = "sale", "return", "exchange"
VOID, POST_VOID = "void", "post_void"
NO_SALE, PAID_IN, PAID_OUT, PULL, LOAN = "no_sale", "paid_in", "paid_out", "pull", "loan"
OPEN, CLOSE, TOTAL, DAY_CLOSE = "open", "close", "total", "day_close"
CONDITION, METER, TERM, REFUND = "condition", "meter", "term", "refund"

MERCHANDISE_TYPES = {SALE, RETURN, EXCHANGE}
CASH_MOVEMENT_TYPES = {PAID_IN, PAID_OUT, PULL, LOAN}

TENDER_TYPES = {
    "CASH": "Cash",
    "CCARD": "Card",
    "DCARD": "Card",
    "CHECK": "Other",
    "COUPON": "Voucher",
    "VOUCH": "Voucher",
    "GCERT": "GiftCard",
    "GIFTCD": "GiftCard",
    "SCRED": "StoreCredit",
    "MORDER": "Other",
}


@dataclass
class StandardLog:
    """A parsed POS log file: its header, its transactions, and anything wrong with the file itself."""

    source_format: str
    store_id: str
    business_date: str  # YYYY-MM-DD
    created_at: str  # ISO-8601
    origin_system: str | None = None
    transactions: list[dict] = field(default_factory=list)
    problems: list[str] = field(
        default_factory=list
    )  # integrity problems: counts that do not add up


def tender_type(group: str) -> str:
    return TENDER_TYPES.get(group.strip().upper(), "Other")


def line_total(line: dict) -> Decimal:
    """Signed value of a line: quantity x unit price, negative for a returned item."""
    return Decimal(line["quantity"]) * Decimal(line["unit_price"])


def without_voided_lines(tx: dict) -> dict:
    """A copy of the transaction with each voided line (status V) removed together with the line it cancels.

    A cashier scans an item and then voids it: the log keeps both lines, so they only net out once paired. A void
    pairs with the earliest unpaired line of the same item, price and opposite quantity. Taxes and discounts that
    belong to a paired line go with it.
    """
    lines = tx["lines"]
    dropped: set[int] = set()
    for at, line in enumerate(lines):
        if line.get("item_status") != "V" or at in dropped:
            continue
        for earlier, other in enumerate(lines):
            if (
                earlier != at
                and earlier not in dropped
                and other.get("item_status") != "V"
                and other["item_code"] == line["item_code"]
                and other["unit_price"] == line["unit_price"]
                and Decimal(other["quantity"]) == -Decimal(line["quantity"])
            ):
                dropped |= {at, earlier}
                break
    if not dropped:
        return tx
    keep = [i for i in range(len(lines)) if i not in dropped]
    renumber = {old + 1: new + 1 for new, old in enumerate(keep)}
    return {
        **tx,
        "lines": [lines[i] for i in keep],
        "discounts": [
            {**d, "line_no": renumber[d["line_no"]]}
            for d in tx["discounts"]
            if d.get("line_no") in renumber
        ],
        "taxes": [],  # rebuilt from the remaining lines
    }


def net_amount(tx: dict) -> Decimal:
    """What the customer owes (positive) or is owed (negative): lines - discounts + tax."""
    gross = sum((line_total(x) for x in tx["lines"]), Decimal("0"))
    discounts = sum((Decimal(x["amount"]) for x in tx["discounts"]), Decimal("0"))
    return gross - discounts + line_tax(tx)


def line_tax(tx: dict) -> Decimal:
    """Tax carried on the lines; falls back to the transaction's own tax records when lines carry none."""
    on_lines = sum((Decimal(x.get("tax_amount", "0")) for x in tx["lines"]), Decimal("0"))
    return on_lines or sum((Decimal(x["amount"]) for x in tx["taxes"]), Decimal("0"))


def direction(tx: dict) -> str | None:
    """'sale' when every line is sold, 'return' when every line is returned, None when mixed or empty."""
    signs = {Decimal(x["quantity"]) < 0 for x in tx["lines"]}
    if signs == {False}:
        return SALE
    if signs == {True}:
        return RETURN
    return None


def settle_tenders(tenders: list[dict], direction_sign: int) -> list[dict]:
    """Payments after change: ingest tenders are positive, so cash change comes off the tender that was overpaid.

    `direction_sign` is +1 for a sale (payments positive, change negative) and -1 for a return (refunds negative,
    so flipping makes them positive). Change is taken from the largest tenders first.
    """
    signed = [
        {**t, "amount": Decimal(t["amount"]) * direction_sign}
        for t in tenders
        if Decimal(t["amount"]) != 0
    ]
    change = -sum((t["amount"] for t in signed if t["amount"] < 0), Decimal("0"))
    paid = sorted((t for t in signed if t["amount"] > 0), key=lambda t: t["amount"], reverse=True)
    for tender in paid:
        take = min(change, tender["amount"])
        tender["amount"] -= take
        change -= take
    return [{**t, "amount": str(t["amount"])} for t in paid if t["amount"] > 0]


def ingest_record(tx: dict, currency: str) -> dict | None:
    """The POS ingest record for a pure sale or return; None for anything else (mixed lines, register events)."""
    tx = without_voided_lines(tx)
    if tx["type"] not in MERCHANDISE_TYPES or not tx["lines"] or direction(tx) is None:
        return None
    kind = direction(tx)
    total = net_amount(tx)
    if total == 0:
        return None
    lines = [
        {
            "item_code": x["item_code"],
            "description": x.get("description"),
            "quantity": str(abs(Decimal(x["quantity"]))),
            "unit_price": x["unit_price"],
            "gross_amount": str(abs(line_total(x))),
            "tax_code": x.get("tax_code"),
            "tax_rate": x.get("tax_rate", "0"),
            "tax_amount": str(abs(Decimal(x.get("tax_amount", "0")))),
            "return_reason": x.get("return_reason"),
        }
        for x in tx["lines"]
    ]
    tenders = [
        {"tender_type": t["tender_type"], "amount": t["amount"], "reference": t.get("reference")}
        for t in settle_tenders(tx["tenders"], 1 if kind == SALE else -1)
    ]
    return {
        "source_record_id": tx["source_record_id"],
        "record_type": "Sale" if kind == SALE else "Return",
        "event_time": tx["event_time"],
        "amount": str(abs(total)),
        "store_id": tx["store_id"],
        "register_id": tx["register_id"] or None,
        "cashier_id": tx["cashier_id"] or None,
        "currency": currency,
        "payment_reference": tx["source_record_id"],
        "tender_type": tenders[0]["tender_type"] if tenders else None,
        "lines": lines,
        "discounts": [
            {k: x.get(k) for k in ("kind", "code", "description", "line_no")}
            | {"amount": str(abs(Decimal(x["amount"])))}
            for x in tx["discounts"]
            if Decimal(x["amount"]) != 0
        ],
        "tenders": tenders,
        **(
            {"return_reason": tx["reason_code"]} if kind == RETURN and tx.get("reason_code") else {}
        ),
    }


def ingest_records(log: StandardLog, currency: str = "USD") -> tuple[list[dict], list[dict]]:
    """(POS ingest records, transactions that did not map: mixed sale-and-return lines, and register events)."""
    mapped, left_over = [], []
    for tx in log.transactions:
        record = ingest_record(tx, currency)
        if record is None:
            left_over.append(tx)
        else:
            mapped.append(record)
    return mapped, left_over


def register_events(log: StandardLog) -> list[dict]:
    """Till and drawer activity with no sale behind it: voids, no-sales, paid-in/out, pulls, loans, opens and closes."""
    return [tx for tx in log.transactions if tx["type"] not in MERCHANDISE_TYPES]
