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
TANK_DIP, PUMP_TEST = "tank_dip", "pump_test"

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


def line_source_attributes(line: dict):
    """Retain the standard item type alongside any vendor-specific line attributes."""
    attributes = line.get("source_attributes")
    item_type = line.get("item_type")
    if item_type in (None, ""):
        return attributes
    if attributes is None:
        return {"item_type": item_type}
    if isinstance(attributes, dict):
        return {**attributes, "item_type": item_type}
    # Keep malformed attributes intact so canonical detail validation reports the right error.
    return attributes


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
    layout: str | None = None  # which published layout variant the file matched
    version: str | None = None  # the producer's version string, for example V.14.0


def tender_type(group: str) -> str:
    return TENDER_TYPES.get(group.strip().upper(), "Other")


# RMS (group, tender type id) pairs the client totals separate (documents/System_ReSA Total setup.xlsx).
CLIENT_TENDERS = {
    ("CASH", "1000"): "CASH",
    ("VOUCH", "4000"): "AGNCYVOU",
    ("VOUCH", "4040"): "GIFTCRDU",
    ("VOUCH", "4050"): "GIFTCRDU",
    ("VOUCH", "4060"): "MALLCERT",
    ("VOUCH", "4120"): "VIRTGC",
    ("CCARD", "3000"): "VISA",
    ("CCARD", "3010"): "MASTERCD",
    ("CCARD", "3020"): "AMEX",
    ("CCARD", "3030"): "DISCOVER",
    ("CCARD", "3120"): "BEALLSCC",
    ("CCARD", "3130"): "OUTLTCC",
    ("OTHERS", "9011"): "EBAY",
    ("OTHERS", "9012"): "AMAZON",
    ("OTHERS", "9013"): "WALMART",
    ("PAYPAL", "9010"): "PAYPAL",
}
CLIENT_TENDER_GROUPS = {"CHECK": "CHECK", "DCARD": "MCVISADBT"}


def client_tender_code(group: str, type_id: str, amount: Decimal) -> str | None:
    """The client's tender code for an RMS tender, or None when the totals do not separate it.

    Gift card and MCR share type ids 4040/4050: a negative tender is the MCR issued on a return.
    """
    key = (group.strip().upper(), str(type_id).strip().lstrip("0") or "0")
    code = CLIENT_TENDERS.get(key) or CLIENT_TENDER_GROUPS.get(key[0])
    if code == "GIFTCRDU" and amount < 0:
        return "MCRISSUE"
    return code


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
            "original_unit_price": x.get("original_unit_price"),
            "price_override_reason": x.get("price_override_reason"),
            "source_attributes": line_source_attributes(x),
            "gross_amount": str(abs(line_total(x))),
            "tax_code": x.get("tax_code"),
            "tax_rate": x.get("tax_rate", "0"),
            "tax_amount": str(abs(Decimal(x.get("tax_amount", "0")))),
            "return_reason": x.get("return_reason"),
        }
        for x in tx["lines"]
    ]
    tenders = [
        {
            "tender_type": t["tender_type"],
            "amount": t["amount"],
            "reference": t.get("reference"),
            "authorization": t.get("authorization"),
            "source_attributes": t.get("source_attributes"),
        }
        for t in settle_tenders(tx["tenders"], 1 if kind == SALE else -1)
    ]
    return {
        "source_record_id": tx["source_record_id"],
        "record_type": "Sale" if kind == SALE else "Return",
        "event_time": tx["event_time"],
        "amount": str(abs(total)),
        "store_id": tx["store_id"],
        "register_id": tx["register_id"] or None,
        "transaction_number": tx.get("transaction_number") or None,
        "cashier_id": tx["cashier_id"] or None,
        "salesperson_id": tx.get("salesperson_id") or None,
        "currency": currency,
        "payment_reference": tx["source_record_id"],
        "details": {
            key: value
            for key, value in (tx.get("details") or {}).items()
            if key
            in {
                "vendor_number",
                "vendor_invoice_number",
                "proof_of_delivery_number",
                "payment_reference_number",
            }
        },
        "user_references": (tx.get("details") or {}).get("user_references"),
        "customer_attributes": (tx.get("details") or {}).get("customer_attributes"),
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


def side(tx: dict, lines: list[dict], suffix: str, tenders: list[dict]) -> dict:
    """One half of an exchange: the lines of one direction, with the discounts that belong to them."""
    keep = {i + 1: n + 1 for n, i in enumerate(i for i, x in enumerate(tx["lines"]) if x in lines)}
    return {
        **tx,
        "source_record_id": f"{tx['source_record_id']}-{suffix}",
        "lines": lines,
        "discounts": [
            {**d, "line_no": keep[d["line_no"]]}
            for d in tx["discounts"]
            if d.get("line_no") in keep
        ],
        "taxes": [],
        "tenders": tenders,
    }


def exchange_records(tx: dict, currency: str) -> list[dict]:
    """A return that also sells an item, as a Return and a Sale.

    The returned goods' value pays for the new goods as store credit; whatever is left over is what the customer
    paid or was refunded, on the tenders actually used. Both records add up to their own amounts.
    """
    tx = without_voided_lines(tx)
    sold = [x for x in tx["lines"] if Decimal(x["quantity"]) > 0]
    returned = [x for x in tx["lines"] if Decimal(x["quantity"]) < 0]
    if tx["type"] not in MERCHANDISE_TYPES or not sold or not returned:
        return []
    sale_total = net_amount(side(tx, sold, "S", []))
    return_total = -net_amount(side(tx, returned, "R", []))
    credit = min(sale_total, return_total)
    net = (
        sale_total - return_total
    )  # > 0: the customer pays the difference; < 0: the customer is refunded it
    paid = settle_tenders(tx["tenders"], 1 if net >= 0 else -1)
    credit_tender = [{"tender_type": "StoreCredit", "amount": str(credit)}] if credit > 0 else []
    sale_tenders = credit_tender + (paid if net > 0 else [])
    return_tenders = [
        {**t, "amount": "-" + t["amount"]} for t in credit_tender + (paid if net < 0 else [])
    ]
    records = [
        ingest_record(side(tx, returned, "R", return_tenders), currency),
        ingest_record(side(tx, sold, "S", sale_tenders), currency),
    ]
    return [r for r in records if r is not None]


def ingest_records(log: StandardLog, currency: str = "USD") -> tuple[list[dict], list[dict]]:
    """(POS ingest records, transactions that did not map: no usable lines, and register events).

    A transaction that returns and sells in one go becomes two records (see `exchange_records`).
    """
    mapped, left_over = [], []
    for tx in log.transactions:
        records = [r] if (r := ingest_record(tx, currency)) else exchange_records(tx, currency)
        if records:
            for record in records:
                record["business_date"] = log.business_date
            mapped.extend(records)
        else:
            left_over.append(tx)
    return mapped, left_over


def register_events(log: StandardLog) -> list[dict]:
    """Till and drawer activity with no sale behind it: voids, no-sales, paid-in/out, pulls, loans, opens and closes."""
    return [tx for tx in log.transactions if tx["type"] not in MERCHANDISE_TYPES]


def unmapped_merchandise(transactions: list[dict]) -> list[dict]:
    """Merchandise transactions that could not become canonical sale or return records."""
    return [tx for tx in transactions if tx["type"] in MERCHANDISE_TYPES]
