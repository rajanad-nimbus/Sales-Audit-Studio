"""Parse a retail transaction log (RTLOG) into the standard POS log.

Layout: Oracle Retail Sales Audit "RTLOG Layout" (Merchandising System Operations Guide, interface file SA RTLOG).
A file is FHEAD, then per transaction THEAD, optional TCUST/CATT, TITEM (each followed by its IDISC and IGTAX), TTAX,
TTEND and TTAIL, then FTAIL. Fixed width, 1-based columns below are cumulative field widths.

Version 14 files differ from the published 10.x layout in one place that matters here: the item record's voucher
number is 25 characters wide (16 in 10.x), which moves every later item column by nine. The sample files in TLOG/
balance to the cent with that width.

Amounts carry four implied decimals and a separate P/N sign column; tax rates are percentages (5.25 means 5.25%).
"""

from datetime import datetime
from decimal import Decimal

from apps.ingest.pos_formats import standard

SIGNED = Decimal("10000")

# (name, width) in file order, from the layout tables. Fields we do not use are named so the offsets stay checkable.
LAYOUTS: dict[str, list[tuple[str, int]]] = {
    "FHEAD": [
        ("descriptor", 5),
        ("line_id", 10),
        ("file_type", 4),
        ("created_at", 14),
        ("business_date", 8),
        ("location", 10),
        ("reference", 30),
        ("origin_system", 3),
    ],
    "THEAD": [
        ("descriptor", 5),
        ("line_id", 10),
        ("register", 5),
        ("event_time", 14),
        ("transaction_number", 10),
        ("cashier", 10),
        ("salesperson", 10),
        ("type", 6),
        ("sub_type", 6),
        ("original_transaction_number", 10),
        ("original_register", 5),
        ("reason_code", 6),
        ("vendor", 10),
        ("vendor_invoice", 30),
        ("payment_reference", 16),
        ("proof_of_delivery", 30),
        ("reference_1", 30),
        ("reference_2", 30),
        ("reference_3", 30),
        ("reference_4", 30),
        ("value_sign", 1),
        ("value", 20),
    ],
    "TITEM": [
        ("descriptor", 5),
        ("line_id", 10),
        ("status", 6),
        ("item_type", 6),
        ("item_number_type", 6),
        ("format_id", 1),
        ("item", 25),
        ("reference_item", 25),
        ("non_merch_item", 25),
        ("voucher", 25),
        ("department", 4),
        ("class", 4),
        ("subclass", 4),
        ("quantity_sign", 1),
        ("quantity", 12),
        ("selling_unit", 4),
        ("unit_retail", 20),
        ("override_reason", 6),
        ("original_unit_retail", 20),
        ("taxable", 1),
        ("pump", 8),
        ("reference_1", 30),
        ("reference_2", 30),
        ("reference_3", 30),
        ("reference_4", 30),
        ("swiped", 1),
        ("return_reason", 6),
        ("salesperson", 10),
        ("expiration", 8),
        ("drop_ship", 1),
    ],
    "IDISC": [
        ("descriptor", 5),
        ("line_id", 10),
        ("promotion", 6),
        ("reference", 10),
        ("discount_type", 6),
        ("coupon", 16),
        ("coupon_reference", 16),
        ("quantity_sign", 1),
        ("quantity", 12),
        ("unit_discount", 20),
    ],
    "IGTAX": [
        ("descriptor", 5),
        ("line_id", 10),
        ("item_sequence", 10),
        ("tax_code", 6),
        ("rate", 20),
        ("amount_sign", 1),
        ("amount", 20),
    ],
    "TTAX": [
        ("descriptor", 5),
        ("line_id", 10),
        ("tax_code", 6),
        ("amount_sign", 1),
        ("amount", 20),
    ],
    "TTEND": [
        ("descriptor", 5),
        ("line_id", 10),
        ("group", 6),
        ("type_id", 6),
        ("sign", 1),
        ("amount", 20),
        ("card_number", 16),
        ("authorization", 16),
        ("authorization_source", 6),
        ("cardholder_verification", 6),
        ("card_expiration", 8),
        ("entry_mode", 6),
        ("terminal", 5),
        ("special_condition", 6),
        ("voucher", 16),
        ("coupon", 16),
        ("coupon_reference", 16),
    ],
    "TTAIL": [("descriptor", 5), ("line_id", 10), ("record_count", 10)],
    "FTAIL": [("descriptor", 5), ("line_id", 10), ("record_count", 10)],
}

# Published layouts differ between releases. v14 is the default; v10 is the 10.x layout from Oracle's operations
# guide, where the item's voucher number is 16 characters wide instead of 25. The file's own item record decides.
LAYOUT_VARIANTS: dict[str, dict[str, list[tuple[str, int]]]] = {
    "v14": LAYOUTS,
    "v10": {**LAYOUTS, "TITEM": [(n, 16 if n == "voucher" else w) for n, w in LAYOUTS["TITEM"]]},
}

TYPES = {
    "SALE": standard.SALE,
    "RETURN": standard.RETURN,
    "EEXCH": standard.EXCHANGE,
    "VOID": standard.VOID,
    "PVOID": standard.POST_VOID,
    "NOSALE": standard.NO_SALE,
    "PAIDIN": standard.PAID_IN,
    "PAIDOU": standard.PAID_OUT,
    "PULL": standard.PULL,
    "LOAN": standard.LOAN,
    "OPEN": standard.OPEN,
    "CLOSE": standard.CLOSE,
    "TOTAL": standard.TOTAL,
    "DCLOSE": standard.DAY_CLOSE,
    "COND": standard.CONDITION,
    "METER": standard.METER,
    "TERM": standard.TERM,
    "REFUND": standard.REFUND,
}


def fields(kind: str, line: str, layouts: dict | None = None) -> dict[str, str]:
    """Cut a fixed-width record into its named fields (a short line leaves the rest empty)."""
    out, at = {}, 0
    for name, width in (layouts or LAYOUTS)[kind]:
        out[name] = line[at : at + width].strip()
        at += width
    return out


def detect_layout(records: list[str]) -> str | None:
    """The layout whose item record reads sensibly: a P/N quantity sign, numeric quantity and unit retail.

    None when the file has no item records (any layout will do) or none of the layouts fits.
    """
    first = next((line for line in records if line.startswith("TITEM")), None)
    if first is None:
        return None
    for name, layouts in LAYOUT_VARIANTS.items():
        f = fields("TITEM", first, layouts)
        if (
            f["quantity_sign"] in ("P", "N")
            and f["quantity"].isdigit()
            and f["unit_retail"].isdigit()
        ):
            return name
    return ""


def signed(sign: str, digits: str, decimals: int = 4) -> Decimal:
    """A P/N sign column and a digit string with implied decimals."""
    if digits and not digits.isdigit():
        raise ValueError(f"expected digits in an amount field, found {digits!r}")
    value = Decimal(digits or "0") / Decimal(10**decimals)
    return -value if sign.upper() == "N" else value


def clean(value: Decimal) -> str:
    """A decimal as text without a long run of trailing zeros: 10.0000 -> 10."""
    text = format(value.normalize(), "f")
    return text if "." not in text else text.rstrip("0").rstrip(".")


def timestamp(raw: str) -> str:
    return datetime.strptime(raw, "%Y%m%d%H%M%S").isoformat() + "+00:00"


def parse_item(f: dict[str, str]) -> dict:
    sold = signed(f["quantity_sign"] or "P", f["quantity"])
    return {
        "item_code": f["item"]
        or f["non_merch_item"]
        or f["voucher"]
        or f["reference_item"]
        or "NO-SKU",
        "item_type": f["item_type"],
        "item_status": f["status"],
        "department": f["department"],
        "class": f["class"],
        "subclass": f["subclass"],
        "quantity": clean(sold),
        "unit_price": clean(signed("P", f["unit_retail"])),
        "taxable": f["taxable"] == "Y",
        "return_reason": f["return_reason"] or None,
        "tax_code": None,
        "tax_rate": "0",
        "tax_amount": "0",
    }


def parse_discount(f: dict[str, str], line_no: int) -> dict:
    amount = signed(f["quantity_sign"] or "P", f["quantity"]) * signed("P", f["unit_discount"])
    return {
        "kind": "Coupon" if f["coupon"] else "Promotion",
        "code": f["coupon"] or f["promotion"] or None,
        "description": f["discount_type"] or None,
        "line_no": line_no,
        "amount": clean(amount),
    }


def parse_tender(f: dict[str, str]) -> dict:
    return {
        "tender_type": standard.tender_type(f["group"]),
        "group": f["group"],
        "type_id": f["type_id"],
        "amount": clean(signed(f["sign"] or "P", f["amount"])),
        "reference": f["voucher"] or f["coupon"] or None,
        "authorization": f["authorization"] or None,
    }


def attach_tax(item: dict, tax: dict[str, str]) -> None:
    """Item-level tax follows its item: add the amount and the rate, keep the first code."""
    amount = signed(tax["amount_sign"] or "P", tax["amount"])
    rate = (
        Decimal(tax["rate"] or "0") / SIGNED / 100
    )  # a percentage with four decimals -> a fraction
    item["tax_amount"] = clean(Decimal(item["tax_amount"]) + amount)
    item["tax_rate"] = clean(Decimal(item["tax_rate"]) + rate)
    item["tax_code"] = item["tax_code"] or tax["tax_code"] or None


def parse_rtlog(text: str, name: str = "RTLOG") -> standard.StandardLog:
    """Parse one RTLOG file's text. Problems with the file itself (counts that do not add up) are listed, not raised."""
    records = [line for line in text.splitlines() if line.strip()]
    if not records or not records[0].startswith("FHEAD"):
        raise ValueError(f"{name}: not an RTLOG file (no FHEAD record)")
    layout = detect_layout(records)
    if layout == "":
        raise ValueError(
            f"{name}: the item records fit none of the known layouts, so amounts cannot be read"
        )
    layouts = LAYOUT_VARIANTS[layout or "v14"]

    def cut(kind: str, line: str) -> dict[str, str]:
        return fields(kind, line, layouts)

    head = cut("FHEAD", records[0])
    business_date = datetime.strptime(head["business_date"], "%Y%m%d").strftime("%Y-%m-%d")
    log = standard.StandardLog(
        source_format="rtlog",
        store_id=head["location"].lstrip("0") or head["location"],
        business_date=business_date,
        created_at=timestamp(head["created_at"]),
        origin_system=head["origin_system"] or None,
        layout=layout or "v14",
        version=head["reference"].split(":")[0].split()[0] if head["reference"] else None,
    )
    tx: dict | None = None
    item: dict | None = None
    in_tx = 0
    for number, line in enumerate(records[1:], 2):
        kind = line[:5]
        if kind == "THEAD":
            f = cut("THEAD", line)
            tx_type = TYPES.get(f["type"])
            if tx_type is None:
                log.problems.append(f"line {number}: unknown transaction type {f['type']!r}")
                tx_type = f["type"].lower()
            tran_no = f["transaction_number"].lstrip("0") or "0"
            # TOTAL and similar records carry no transaction number, and a till close writes several in the same
            # second, so their place in the file is what tells them apart (stable when the file is delivered again).
            suffix = f"-{f['event_time']}-L{int(f['line_id'] or 0)}" if tran_no == "0" else ""
            tx = {
                "source_record_id": f"RTLOG-{log.store_id}-{business_date}-{f['register']}-{tran_no}{suffix}",
                "store_id": log.store_id,
                "business_date": business_date,
                "register_id": f["register"],
                "transaction_number": tran_no,
                "event_time": timestamp(f["event_time"]),
                "cashier_id": f["cashier"],
                "salesperson_id": f["salesperson"],
                "type": tx_type,
                "rtlog_type": f["type"],
                "sub_type": f["sub_type"],
                "original_transaction_number": f["original_transaction_number"].lstrip("0") or None,
                "original_register_id": f["original_register"] or None,
                "reason_code": f["reason_code"] or None,
                "vendor": f["vendor"] or None,
                "value": clean(signed(f["value_sign"] or "P", f["value"])) if f["value"] else None,
                "lines": [],
                "discounts": [],
                "taxes": [],
                "tenders": [],
            }
            item, in_tx = None, 1
        elif kind == "FTAIL":
            expected = int(cut("FTAIL", line)["record_count"] or 0)
            if expected != len(records) - 2:
                log.problems.append(f"FTAIL says {expected} records, found {len(records) - 2}")
        elif tx is None:
            log.problems.append(f"line {number}: {kind} before any THEAD")
        elif kind == "TITEM":
            item = parse_item(cut("TITEM", line))
            tx["lines"].append(item)
            in_tx += 1
        elif kind == "IDISC" and item is not None:
            tx["discounts"].append(parse_discount(cut("IDISC", line), len(tx["lines"])))
            in_tx += 1
        elif kind == "IGTAX" and item is not None:
            attach_tax(item, cut("IGTAX", line))
            in_tx += 1
        elif kind == "TTAX":
            f = cut("TTAX", line)
            tx["taxes"].append(
                {
                    "code": f["tax_code"],
                    "amount": clean(signed(f["amount_sign"] or "P", f["amount"])),
                }
            )
            in_tx += 1
        elif kind == "TTEND":
            tx["tenders"].append(parse_tender(cut("TTEND", line)))
            in_tx += 1
        elif kind == "TTAIL":
            expected = int(cut("TTAIL", line)["record_count"] or 0)
            if expected != in_tx - 1:
                log.problems.append(
                    f"transaction {tx['transaction_number']}: TTAIL says {expected} records, found {in_tx - 1}"
                )
            finish(tx)
            log.transactions.append(tx)
            tx = item = None
        elif kind in ("TCUST", "CATT"):
            in_tx += 1
        else:
            log.problems.append(f"line {number}: unknown record {kind!r}")
    if tx is not None:
        log.problems.append(f"transaction {tx['transaction_number']}: no TTAIL")
    if not records[-1].startswith("FTAIL"):
        log.problems.append("no FTAIL record")
    return log


def finish(tx: dict) -> None:
    """Derive the transaction-level tax list from the item taxes, so lines and tax always add up."""
    totals: dict[str, Decimal] = {}
    for line in tx["lines"]:
        if Decimal(line["tax_amount"]):
            code = line["tax_code"] or "TAX"
            totals[code] = totals.get(code, Decimal(0)) + Decimal(line["tax_amount"])
    tx["taxes"] = tx["taxes"] or [
        {"code": code, "amount": clean(amount)} for code, amount in totals.items()
    ]


def parse_rtlog_file(path) -> standard.StandardLog:
    with open(path, encoding="ascii", errors="replace") as handle:
        return parse_rtlog(handle.read(), name=str(path))
