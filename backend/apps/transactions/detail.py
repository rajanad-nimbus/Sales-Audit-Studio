"""Line-level detail of a sales transaction: items, tax, discounts (promotion, coupon, voucher), tenders, returns.

A source record may carry the detail. It is validated when the record arrives (a malformed block quarantines the
record, like any other bad field) and checked against the header amount. A detail block that does not add up is kept
and flagged Unbalanced, because that is an audit finding and not a reason to hide the sale.
"""
from __future__ import annotations

import random
from decimal import Decimal, InvalidOperation

CENT = Decimal("0.01")
MAX_MONEY = Decimal("9999999999999.99")
MAX_QUANTITY = Decimal("999999999.999")
DISCOUNT_KINDS = ("Promotion", "Coupon", "Voucher", "Manual")
TENDER_TYPES = ("Card", "Cash", "Voucher", "GiftCard", "StoreCredit", "Other")
DETAIL_KEYS = ("lines", "discounts", "taxes", "tenders")
TEXT_LIMITS = {
    "item_code": 100,
    "line description": 255,
    "line tax_code": 30,
    "line return_reason": 255,
    "tax_code": 30,
    "tax_name": 100,
    "discount code": 100,
    "discount description": 255,
    "tender reference": 255,
    "tender authorization": 100,
    "original_reference": 255,
    "return_reason": 255,
}


def optional_text(value, field: str) -> str | None:
    """Validate nullable text against the corresponding persisted column."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    if len(value) > TEXT_LIMITS[field]:
        raise ValueError(f"{field} exceeds {TEXT_LIMITS[field]} characters")
    return value or None


def decimal_value(value, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{field} is not a decimal")
    if not result.is_finite():
        raise ValueError(f"{field} is not a finite decimal")
    return result


def cent_value(value, field: str) -> Decimal:
    """Parse a monetary value only when converting it to cents is exact."""
    amount = decimal_value(value, field)
    try:
        rounded = amount.quantize(CENT)
    except InvalidOperation as exc:
        raise ValueError(f"{field} exceeds supported monetary precision") from exc
    if amount != rounded:
        raise ValueError(f"{field} has more than two decimal places")
    return rounded


def money(value, field: str, allow_zero=True) -> Decimal:
    d = cent_value(value, field)
    if d < 0 or (d == 0 and not allow_zero):
        raise ValueError(f"{field} must be {'positive' if not allow_zero else 'zero or more'}")
    if d > MAX_MONEY:
        raise ValueError(f"{field} exceeds supported precision")
    return d


def has_detail(rec: dict) -> bool:
    supplied_detail = any(
        key in rec and rec[key] is not None and rec[key] != []
        for key in DETAIL_KEYS
    )
    supplied_return_metadata = any(
        key in rec and rec[key] is not None
        for key in ("original_reference", "return_reason")
    )
    return supplied_detail or supplied_return_metadata


def extract(rec: dict, sign: int) -> dict | None:
    """Validate and normalize the detail of one source record. Amounts are signed like the header (returns negative)."""
    if not has_detail(rec):
        return None
    out = {"lines": [], "discounts": [], "taxes": [], "tenders": [],
           "original_reference": (optional_text(rec.get("original_reference"), "original_reference") or "").strip() or None,
           "return_reason": (optional_text(rec.get("return_reason"), "return_reason") or "").strip() or None}
    for key in DETAIL_KEYS:
        if rec.get(key) is not None and not isinstance(rec[key], list):
            raise ValueError(f"{key} must be a list")

    for n, ln in enumerate(rec.get("lines") or [], 1):
        if not isinstance(ln, dict):
            raise ValueError(f"line {n} must be an object")
        item_code = optional_text(ln.get("item_code"), "item_code")
        if not item_code or not item_code.strip():
            raise ValueError(f"line {n} has no item_code")
        description = optional_text(ln.get("description"), "line description")
        tax_code = optional_text(ln.get("tax_code"), "line tax_code")
        return_reason = optional_text(ln.get("return_reason"), "line return_reason")
        qty = decimal_value(ln.get("quantity"), f"line {n} quantity")
        if qty <= 0:
            raise ValueError(f"line {n} quantity must be positive")
        if qty > MAX_QUANTITY or qty != qty.quantize(Decimal("0.001")):
            raise ValueError(f"line {n} quantity exceeds supported precision")
        price = money(ln.get("unit_price"), f"line {n} unit_price")
        gross = money(ln["gross_amount"], f"line {n} gross_amount") if ln.get("gross_amount") is not None else money(qty * price, f"line {n} gross_amount")
        rate = decimal_value(ln.get("tax_rate", 0), f"line {n} tax_rate")
        if rate < 0 or rate > 1 or rate != rate.quantize(Decimal("0.0001")):
            raise ValueError(f"line {n} tax_rate must be between 0 and 1")
        tax = money(ln["tax_amount"], f"line {n} tax_amount") if ln.get("tax_amount") is not None else money(gross * rate, f"line {n} tax_amount")
        out["lines"].append({"line_no": n, "item_code": item_code.strip(), "description": description,
                             "quantity": qty * sign, "unit_price": price, "gross_amount": gross * sign, "tax_code": tax_code,
                             "tax_rate": rate, "tax_amount": tax * sign, "return_reason": return_reason})

    for n, d in enumerate(rec.get("discounts") or [], 1):
        if not isinstance(d, dict):
            raise ValueError(f"discount {n} must be an object")
        kind = d.get("kind")
        if kind not in DISCOUNT_KINDS:
            raise ValueError(f"discount {n} kind must be one of {', '.join(DISCOUNT_KINDS)}")
        line_no = d.get("line_no")
        code = optional_text(d.get("code"), "discount code")
        description = optional_text(d.get("description"), "discount description")
        if line_no is not None and not (type(line_no) is int and 1 <= line_no <= len(out["lines"])):
            raise ValueError(f"discount {n} refers to a line that does not exist")
        out["discounts"].append({"kind": kind, "code": code, "description": description, "line_no": line_no,
                                 "amount": money(d.get("amount"), f"discount {n} amount", allow_zero=False) * sign})

    for n, t in enumerate(rec.get("tenders") or [], 1):
        if not isinstance(t, dict):
            raise ValueError(f"tender {n} must be an object")
        if t.get("tender_type") not in TENDER_TYPES:
            raise ValueError(f"tender {n} tender_type must be one of {', '.join(TENDER_TYPES)}")
        reference = optional_text(t.get("reference"), "tender reference")
        authorization = optional_text(t.get("authorization"), "tender authorization")
        out["tenders"].append({"tender_type": t["tender_type"], "reference": reference, "authorization": authorization,
                               "amount": money(t.get("amount"), f"tender {n} amount", allow_zero=False) * sign})

    if rec.get("taxes"):
        for n, x in enumerate(rec["taxes"], 1):
            if not isinstance(x, dict) or not x.get("tax_code"):
                raise ValueError(f"tax {n} needs a tax_code")
            tax_code = optional_text(x.get("tax_code"), "tax_code")
            tax_name = optional_text(x.get("tax_name"), "tax_name") or tax_code
            rate = decimal_value(x.get("rate", 0), f"tax {n} rate")
            if not 0 <= rate <= 1 or rate != rate.quantize(Decimal("0.0001")):
                raise ValueError(f"tax {n} rate must be between 0 and 1")
            out["taxes"].append({"tax_code": tax_code, "tax_name": tax_name, "rate": rate,
                                 "taxable_amount": money(x.get("taxable_amount", 0), f"tax {n} taxable_amount") * sign,
                                 "tax_amount": money(x.get("tax_amount"), f"tax {n} tax_amount") * sign})
    else:   # summarize from the lines
        by: dict = {}
        for ln in out["lines"]:
            if ln["tax_code"] and ln["tax_amount"]:
                s = by.setdefault(ln["tax_code"], {"tax_code": ln["tax_code"], "tax_name": ln["tax_code"], "rate": ln["tax_rate"],
                                                    "taxable_amount": Decimal("0"), "tax_amount": Decimal("0")})
                s["taxable_amount"] += ln["gross_amount"]
                s["tax_amount"] += ln["tax_amount"]
                if abs(s["taxable_amount"]) > MAX_MONEY or abs(s["tax_amount"]) > MAX_MONEY:
                    raise ValueError("summarized tax exceeds supported precision")
        out["taxes"] = list(by.values())
    return out


def totals(detail: dict) -> dict:
    """Sums of a normalized detail block, signed like the header."""
    gross = sum((l["gross_amount"] for l in detail["lines"]), Decimal("0"))
    disc = sum((d["amount"] for d in detail["discounts"]), Decimal("0"))
    tax = sum((l["tax_amount"] for l in detail["lines"]), Decimal("0")) if detail["lines"] else sum((t["tax_amount"] for t in detail["taxes"]), Decimal("0"))
    tendered = sum((t["amount"] for t in detail["tenders"]), Decimal("0"))
    return {"gross": gross, "discounts": disc, "tax": tax, "net": gross - disc, "expected_total": gross - disc + tax, "tendered": tendered}


def check(detail: dict | None, header_amount: Decimal) -> dict:
    """Does the detail add up to the header? Returns status, the variances and what was compared."""
    if not detail or not (detail["lines"] or detail["tenders"]):
        return {"status": "No detail", "total_variance": None, "tender_variance": None}
    t = totals(detail)
    return check_totals(
        t["gross"], t["discounts"], t["tax"], t["tendered"], header_amount,
        has_lines=bool(detail["lines"]), has_tenders=bool(detail["tenders"]),
    )


def check_totals(
    gross: Decimal,
    discounts: Decimal,
    tax: Decimal,
    tendered: Decimal,
    header_amount: Decimal,
    *,
    has_lines: bool,
    has_tenders: bool,
) -> dict:
    """Check pre-aggregated detail totals without loading every child row into memory."""
    if not (has_lines or has_tenders):
        return {"status": "No detail", "total_variance": None, "tender_variance": None}
    expected_total = gross - discounts + tax
    total_var = expected_total - header_amount if has_lines else None
    tender_var = tendered - header_amount if has_tenders else None
    bad = (total_var is not None and abs(total_var) > CENT) or (tender_var is not None and abs(tender_var) > CENT)
    return {
        "status": "Unbalanced" if bad else "Balanced",
        "total_variance": total_var,
        "tender_variance": tender_var,
        "gross": gross,
        "discounts": discounts,
        "tax": tax,
        "net": gross - discounts,
        "expected_total": expected_total,
        "tendered": tendered,
    }


def card_amount(tenders: list, header_amount: Decimal) -> Decimal:
    """What the card processor should have captured: the card tender, or the whole amount when none was recorded."""
    if not tenders:
        return header_amount
    return sum((t["amount"] for t in tenders if t["tender_type"] == "Card"), Decimal("0"))


# ---- synthetic detail for the demo feed: always adds up to the amount it is given ----------------------------------
CATALOG = [("SKU-1001", "Basmati rice 5kg", "12.40"), ("SKU-1002", "Cooking oil 1L", "3.80"), ("SKU-1003", "Tea leaves 250g", "4.50"),
           ("SKU-1004", "Notebook A5", "1.90"), ("SKU-1005", "LED bulb 9W", "2.75"), ("SKU-1006", "Dish soap 500ml", "2.20"),
           ("SKU-1007", "School bag", "18.00"), ("SKU-1008", "Water bottle 1L", "5.60")]
PROMOS = [("PROMO-SPRING10", "Spring promotion 10% off"), ("PROMO-3FOR2", "Three for two")]
COUPONS = [("CPN-WELCOME5", "Welcome coupon"), ("CPN-LOYAL", "Loyalty coupon")]
VOUCHERS = [("VCH-GIFT-2041", "Gift voucher"), ("VCH-REFUND-77", "Refund voucher")]
VAT = ("VAT13", Decimal("0.13"))


def synthesize(total: Decimal, rng: random.Random, sign_positive: bool = True, original_reference: str | None = None,
               return_reason: str | None = None, voucher_tender: bool = False) -> dict:
    """A positive-amount detail block (the shape feeds send) whose lines, tax and discounts add up to `total`.

    total = gross - discounts + tax, tax = total - net so it always balances; tenders pay the whole amount, or the
    card part plus a voucher when `voucher_tender` is set."""
    rate = VAT[1]
    net = (total / (1 + rate)).quantize(CENT)
    tax = total - net
    discounts, disc_total = [], Decimal("0")
    if total > Decimal("40") and rng.random() < 0.55:
        kinds = rng.sample(["Promotion", "Coupon", "Voucher"], k=rng.choice([1, 1, 2]))
        for kind in kinds:
            amt = (net * Decimal(rng.choice(["0.03", "0.05", "0.08"]))).quantize(CENT)
            if amt <= 0:
                continue
            code, desc = rng.choice({"Promotion": PROMOS, "Coupon": COUPONS, "Voucher": VOUCHERS}[kind])
            discounts.append({"kind": kind, "code": code, "description": desc, "amount": str(amt)})
            disc_total += amt
    gross = net + disc_total
    n_lines = 1 if gross < Decimal("15") else rng.randint(1, 4)
    lines, remaining = [], gross
    for i in range(n_lines):
        code, desc, price = rng.choice(CATALOG)
        if i == n_lines - 1:
            qty, unit = Decimal("1"), remaining
        else:
            unit = Decimal(price)
            qty = Decimal(rng.randint(1, 3))
            if qty * unit >= remaining - Decimal("1"):
                qty, unit = Decimal("1"), (remaining / 2).quantize(CENT)
            remaining -= qty * unit
        lines.append({"item_code": code, "description": desc, "quantity": str(qty), "unit_price": str(unit.quantize(CENT)),
                      "tax_code": VAT[0], "tax_rate": str(rate)})
    # Tax per line in proportion to its share, the last line absorbing rounding so the sum equals the header tax.
    acc = Decimal("0")
    for i, ln in enumerate(lines):
        share = (Decimal(ln["quantity"]) * Decimal(ln["unit_price"]) - Decimal("0"))
        ln["tax_amount"] = str((tax - acc) if i == len(lines) - 1 else (tax * share / gross).quantize(CENT))
        acc += Decimal(ln["tax_amount"])
    detail = {"lines": lines, "discounts": discounts,
              "tenders": [{"tender_type": "Card", "amount": str(total), "reference": "****4242"}]}
    if voucher_tender and total > Decimal("30"):
        part = (total * Decimal("0.2")).quantize(CENT)
        detail["tenders"] = [{"tender_type": "Card", "amount": str(total - part), "reference": "****4242"},
                             {"tender_type": "Voucher", "amount": str(part), "reference": rng.choice(VOUCHERS)[0]}]
    if original_reference:
        detail["original_reference"] = original_reference
        detail["return_reason"] = return_reason or "Customer changed mind"
    return detail


def synthesize_return(original: dict, rng: random.Random, extra_qty: int = 0, reason: str = "Customer changed mind") -> tuple[Decimal, dict]:
    """A return of the first line of a sale feed record. extra_qty > 0 returns more than was sold (an audit finding).

    Returns (total refunded, detail block). The refund is the line price plus tax, paid back to the card."""
    line = original["lines"][0]
    qty = Decimal(line["quantity"]) + extra_qty if extra_qty else Decimal("1")
    qty = min(qty, Decimal(line["quantity"])) if not extra_qty else qty
    unit, rate = Decimal(line["unit_price"]), Decimal(line.get("tax_rate", "0"))
    gross = (qty * unit).quantize(CENT)
    tax = (gross * rate).quantize(CENT)
    total = gross + tax
    detail = {"lines": [{"item_code": line["item_code"], "description": line.get("description"), "quantity": str(qty), "unit_price": str(unit),
                         "tax_code": line.get("tax_code"), "tax_rate": str(rate), "tax_amount": str(tax), "return_reason": reason}],
              "discounts": [], "tenders": [{"tender_type": "Card", "amount": str(total), "reference": "****4242"}],
              "original_reference": original["source_record_id"], "return_reason": reason}
    return total, detail
