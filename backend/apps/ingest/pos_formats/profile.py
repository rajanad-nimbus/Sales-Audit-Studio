"""What a POS actually sends in its log, and which audit rules that lets us run.

A log format defines far more than any one POS fills in, so integrating a POS starts from its own file: which
records appear, which fields carry data, which transaction types and tenders it uses. The rule capabilities follow
from that, so a rule that cannot fire for a POS says so instead of silently never raising anything.
"""

from collections import Counter

from apps.ingest.pos_formats import rtlog, standard

IDENTIFIERS = {"descriptor", "line_id"}
OPTIONAL_RECORDS = ("TCUST", "CATT", "TTAX")  # defined by the layout, often not sent
AVAILABLE, NOT_AVAILABLE, NOTHING = "available", "not_available", "nothing_to_audit"


def filled(value: str) -> bool:
    """A field carries data when it is more than blanks and zeros."""
    return bool(value.strip("0 "))


def field_fill(kind: str, lines: list[str], layouts: dict) -> dict[str, list[str]]:
    """Fields of one record type that are filled on (almost) every record, on some, and on none."""
    counts: Counter = Counter()
    for line in lines:
        for name, value in rtlog.fields(kind, line, layouts).items():
            if name not in IDENTIFIERS and filled(value):
                counts[name] += 1
    names = [n for n, _ in layouts[kind] if n not in IDENTIFIERS]
    total = len(lines)
    return {
        "always": [n for n in names if counts[n] and counts[n] >= total * 0.9],
        "sometimes": [n for n in names if counts[n] and counts[n] < total * 0.9],
        "never": [n for n in names if not counts[n]],
    }


def rule_capabilities(types: Counter, fill: dict[str, dict], records: Counter) -> list[dict]:
    """For each audit rule: can it run on what this POS sends, and if not, why."""

    def sends(kind: str, name: str) -> bool:
        return name in fill.get(kind, {}).get("always", []) + fill.get(kind, {}).get(
            "sometimes", []
        )

    def rule(code, status, why):
        return {"code": code, "status": status, "why": why}

    out = []
    both = records["TITEM"] and records["TTEND"]
    out.append(
        rule(
            "TRAN_OUT_BAL", AVAILABLE, "Items and tenders are both sent, so totals can be checked."
        )
        if both
        else rule(
            "TRAN_OUT_BAL", NOT_AVAILABLE, "Needs item and tender records; one of them is missing."
        )
    )
    out.append(
        rule("SKU_NOT_FOUND", AVAILABLE, "Item identifiers are sent.")
        if records["TITEM"] and sends("TITEM", "item")
        else rule("SKU_NOT_FOUND", NOT_AVAILABLE, "No item identifiers are sent.")
    )
    taxed = sends("IGTAX", "rate") or records["TTAX"]
    out.append(
        rule("TAX_MISMATCH", AVAILABLE, "Tax rates are sent with the tax.")
        if taxed
        else rule(
            "TAX_MISMATCH",
            NOT_AVAILABLE,
            "No tax rates are sent, so tax cannot be checked against a rate.",
        )
    )
    out.append(
        rule(
            "DISC_REASON_REQ",
            NOT_AVAILABLE,
            "This format sends promotions and coupons, never manual discounts.",
        )
        if records["IDISC"]
        else rule("DISC_REASON_REQ", NOTHING, "No discounts are sent.")
    )
    if types[standard.RETURN]:
        out.append(
            rule(
                "RETURN_OVER_SOLD",
                NOT_AVAILABLE,
                "Returns carry no original transaction, so a return cannot be tied to its sale.",
            )
        )
    else:
        out.append(rule("RETURN_OVER_SOLD", NOTHING, "No returns are sent."))
    if types[standard.POST_VOID]:
        out.append(
            rule("VOID_NO_ORIG", AVAILABLE, "Post-voids name their original transaction.")
            if sends("THEAD", "original_transaction_number")
            else rule(
                "VOID_NO_ORIG",
                NOT_AVAILABLE,
                "Post-voids are sent without an original transaction.",
            )
        )
    else:
        out.append(rule("VOID_NO_ORIG", NOTHING, "No post-voids are sent."))
    if types[standard.PAID_IN] or types[standard.PAID_OUT]:
        out.append(
            rule(
                "PAIDOUT_REASON_REQ", AVAILABLE, "Paid-in and paid-out are sent with reason codes."
            )
            if sends("THEAD", "reason_code")
            else rule(
                "PAIDOUT_REASON_REQ",
                AVAILABLE,
                "Paid-in and paid-out are sent without reason codes, so all would be flagged.",
            )
        )
    else:
        out.append(rule("PAIDOUT_REASON_REQ", NOTHING, "No paid-in or paid-out is sent."))
    out.append(
        rule("NOSALE_EXCESS", AVAILABLE, "No-sales are sent.")
        if types[standard.NO_SALE]
        else rule("NOSALE_EXCESS", NOTHING, "No no-sales are sent.")
    )
    return out


def profile_rtlog(text: str, name: str = "RTLOG") -> dict:
    """Profile one RTLOG file: its layout, what it contains, which fields carry data, and what we can audit."""
    log = rtlog.parse_rtlog(text, name)
    records = [line for line in text.splitlines() if line.strip()]
    layouts = rtlog.LAYOUT_VARIANTS[log.layout or "v14"]
    by_kind: dict[str, list[str]] = {}
    for line in records:
        by_kind.setdefault(line[:5], []).append(line)
    counts = Counter({kind: len(lines) for kind, lines in by_kind.items()})
    types = Counter(tx["type"] for tx in log.transactions)
    fill = {
        kind: field_fill(kind, by_kind[kind], layouts)
        for kind in ("THEAD", "TITEM", "IDISC", "IGTAX", "TTEND")
        if kind in by_kind
    }
    return {
        "format": "rtlog",
        "layout": log.layout,
        "version": log.version,
        "origin_system": log.origin_system,
        "store_id": log.store_id,
        "business_date": log.business_date,
        "transactions": len(log.transactions),
        "transaction_types": dict(types),
        "tender_groups": dict(
            Counter(t["group"] for tx in log.transactions for t in tx["tenders"])
        ),
        "item_types": dict(
            Counter(line["item_type"] for tx in log.transactions for line in tx["lines"])
        ),
        "records": dict(counts),
        "records_not_sent": [kind for kind in OPTIONAL_RECORDS if not counts[kind]],
        "fields": fill,
        "rules": rule_capabilities(types, fill, counts),
        "problems": log.problems,
    }
