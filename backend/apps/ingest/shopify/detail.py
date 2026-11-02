"""Line, discount and tender detail for Shopify orders and refunds.

Everything here is pure: it maps payloads Shopify already returned to the ingest detail contract
(see apps.transactions.detail) and never calls the API. Lines are built so that
gross - discounts + tax equals the order total, which lets the detail check flag real variances
(overpaid tender, mismatched tax) without false alarms on ordinary orders.
"""

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
ZERO = Decimal("0")
SUCCESSFUL_SALE_KINDS = ("sale", "capture")
SETTLED_STATUSES = ("paid", "partially_refunded", "refunded")

TENDER_BY_GATEWAY = (
    ("gift", "GiftCard"),
    ("exchange_credit", "StoreCredit"),
    ("store_credit", "StoreCredit"),
    ("cash", "Cash"),
    ("voucher", "Voucher"),
    ("manual", "Card"),
    ("shopify_payments", "Card"),
    ("bogus", "Card"),
    ("card", "Card"),
    ("paypal", "Other"),
)
DISCOUNT_KIND = {"discount_code": "Coupon", "automatic": "Promotion", "manual": "Manual"}


def dec(value) -> Decimal:
    return Decimal(str(value if value not in (None, "") else 0))


def cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def tender_type(gateway) -> str:
    name = str(gateway or "").lower()
    return next((kind for key, kind in TENDER_BY_GATEWAY if key in name), "Other")


def item_code(line_item: dict) -> str:
    """The SKU, else the variant code the reference sync uses; an order line with neither is visible as NO-SKU."""
    sku = str(line_item.get("sku") or "").strip()
    if sku:
        return sku[:100]
    variant = line_item.get("variant_id")
    return f"SHOPIFY-VAR-{variant}" if variant else "NO-SKU"


def line_tax(line_item: dict) -> tuple[Decimal, Decimal, str | None]:
    """Tax amount, combined rate and first tax title Shopify reports on one line."""
    taxes = line_item.get("tax_lines") or []
    amount = sum((dec(t.get("price")) for t in taxes), ZERO)
    rate = sum((dec(t.get("rate")) for t in taxes), ZERO)
    return amount, rate, (str(taxes[0].get("title"))[:30] if taxes else None)


def allocated_discount(line_item: dict) -> Decimal:
    allocations = line_item.get("discount_allocations") or []
    if allocations:
        return sum((dec(a.get("amount")) for a in allocations), ZERO)
    return dec(line_item.get("total_discount"))


def spread(total: Decimal, weights: list[Decimal]) -> list[Decimal]:
    """Split `total` in proportion to `weights`; the last non-zero weight takes the rounding remainder."""
    weight_sum = sum(weights, ZERO)
    shares = [cents(total * w / weight_sum) if weight_sum else ZERO for w in weights]
    last = max((i for i, w in enumerate(weights) if w), default=None)
    if last is not None:
        shares[last] += total - sum(shares, ZERO)
    return shares


def order_lines(order: dict) -> list[dict]:
    """One line per item plus one per shipping charge, with tax allocated so the lines add up."""
    inclusive = bool(order.get("taxes_included"))
    items = order.get("line_items") or []
    shipping = order.get("shipping_lines") or []
    rows = [(i, "item") for i in items] + [(s, "shipping") for s in shipping]

    explicit = [line_tax(row) if row else (ZERO, ZERO, None) for row, _ in rows]
    order_taxes = order.get("tax_lines") or []
    # total_tax is what Shopify charged and what total_price includes; tax_lines (on the order or
    # its lines) can list tax that was recorded but never collected, so they only supply the rates.
    charged = dec(order["total_tax"]) if order.get("total_tax") is not None else None
    listed = sum((amount for amount, _, _ in explicit), ZERO)
    if charged is not None and listed and listed != charged:
        shares = spread(charged, [amount for amount, _, _ in explicit])
        explicit = [
            (share, rate if share else ZERO, code if share else None)
            for share, (_, rate, code) in zip(shares, explicit, strict=True)
        ]
    order_tax_total = (
        charged if charged is not None else sum((dec(t.get("price")) for t in order_taxes), ZERO)
    )
    if order_tax_total and not any(amount for amount, _, _ in explicit):
        # Only header-level tax is available: spread it across the taxable lines.
        taxable = [
            n
            for n, (row, kind) in enumerate(rows)
            if (row.get("taxable", True) if kind == "item" else False)
        ] or [n for n, (_, kind) in enumerate(rows) if kind == "shipping"]
        weights = [ZERO] * len(rows)
        for n in taxable:
            row, kind = rows[n]
            base = dec(row.get("price")) * (row.get("quantity", 1) if kind == "item" else 1)
            weights[n] = base - (allocated_discount(row) if kind == "item" else ZERO)
        shares = spread(order_tax_total, weights)
        rate = sum((dec(t.get("rate")) for t in order_taxes), ZERO)
        code = str(order_taxes[0].get("title") or "TAX")[:30]
        explicit = [(s, rate if s else ZERO, code if s else None) for s in shares]

    lines = []
    for (row, kind), (tax, rate, code) in zip(rows, explicit, strict=True):
        quantity = int(row.get("quantity", 1)) if kind == "item" else 1
        price = dec(row.get("price"))
        gross = price * quantity - (tax if inclusive else ZERO)
        lines.append(
            {
                "item_code": item_code(row) if kind == "item" else "SHIPPING",
                "description": str(row.get("title") or "")[:255] or None,
                "quantity": str(quantity),
                "unit_price": str(price),
                "gross_amount": str(cents(gross)),
                "tax_code": code,
                "tax_rate": str(rate.quantize(Decimal("0.0001"))),
                "tax_amount": str(cents(tax)),
            }
        )
    return lines


def order_discounts(order: dict) -> list[dict]:
    """Discounts per application (code, automatic, manual), pointing at a line when it targets exactly one."""
    items = order.get("line_items") or []
    shipping = order.get("shipping_lines") or []
    applications = order.get("discount_applications") or []
    if applications:
        amounts: dict[int, Decimal] = {}
        targets: dict[int, set] = {}
        for line_no, row in enumerate(items + shipping, 1):
            for allocation in row.get("discount_allocations") or []:
                index = allocation.get("discount_application_index", 0)
                amounts[index] = amounts.get(index, ZERO) + dec(allocation.get("amount"))
                targets.setdefault(index, set()).add(line_no)
        out = []
        for index, application in enumerate(applications):
            amount = amounts.get(index, ZERO)
            if amount <= 0:
                continue
            line_nos = targets.get(index, set())
            out.append(
                {
                    "kind": DISCOUNT_KIND.get(application.get("type"), "Promotion"),
                    "code": str(application.get("code") or application.get("title") or "")[:100]
                    or None,
                    "description": str(
                        application.get("description") or application.get("title") or ""
                    )[:255]
                    or None,
                    "line_no": next(iter(line_nos)) if len(line_nos) == 1 else None,
                    "amount": str(cents(amount)),
                }
            )
        return out
    total = dec(order.get("total_discounts"))
    if total <= 0:
        return []
    codes = order.get("discount_codes") or []
    return [
        {
            "kind": "Coupon" if codes else "Manual",
            "code": str(codes[0].get("code"))[:100] if codes else None,
            "description": None,
            "line_no": None,
            "amount": str(cents(total)),
        }
    ]


def order_tenders(order: dict, transactions: list[dict] | None = None) -> list[dict]:
    """Tenders from the order's payment transactions, else the single gateway on a settled order."""
    if transactions is not None:
        return [
            {
                "tender_type": tender_type(t.get("gateway")),
                "amount": str(cents(dec(t.get("amount")))),
                "reference": str(t["id"]) if t.get("id") else None,
                "authorization": t.get("authorization"),
            }
            for t in transactions
            if t.get("kind") in SUCCESSFUL_SALE_KINDS
            and t.get("status", "success") == "success"
            and dec(t.get("amount")) > 0
        ]
    gateways = order.get("payment_gateway_names") or []
    if len(gateways) == 1 and order.get("financial_status") in SETTLED_STATUSES:
        return [
            {
                "tender_type": tender_type(gateways[0]),
                "amount": str(cents(dec(order.get("total_price")))),
            }
        ]
    return []


def order_detail(order: dict, transactions: list[dict] | None = None) -> dict:
    return {
        "lines": order_lines(order),
        "discounts": order_discounts(order),
        "tenders": order_tenders(order, transactions),
    }


def refund_detail(refund: dict, order_id) -> dict:
    """Refund tenders and return reference; lines only when they explain the whole refund."""
    refunded = [
        t
        for t in refund.get("transactions") or []
        if t.get("kind") == "refund"
        and t.get("status", "success") == "success"
        and dec(t.get("amount")) > 0
    ]
    tenders = [
        {
            "tender_type": tender_type(t.get("gateway")),
            "amount": str(cents(dec(t.get("amount")))),
            "reference": str(t["id"]) if t.get("id") else None,
        }
        for t in refunded
    ]
    lines = [
        {
            "item_code": item_code(li.get("line_item") or {}),
            "description": str((li.get("line_item") or {}).get("title") or "")[:255] or None,
            "quantity": str(li.get("quantity", 1)),
            "unit_price": str(dec((li.get("line_item") or {}).get("price"))),
            "gross_amount": str(cents(dec(li.get("subtotal")))),
            "tax_amount": str(cents(dec(li.get("total_tax")))),
            "tax_rate": "0",
            "return_reason": refund.get("note"),
        }
        for li in refund.get("refund_line_items") or []
        if dec(li.get("quantity")) > 0
    ]
    refunded_total = sum((dec(t["amount"]) for t in tenders), ZERO)
    explained = sum((dec(ln["gross_amount"]) + dec(ln["tax_amount"]) for ln in lines), ZERO)
    detail = {
        "lines": lines if lines and explained == refunded_total else [],
        "discounts": [],
        "tenders": tenders,
        "original_reference": str(order_id),
    }
    if refund.get("note"):
        detail["return_reason"] = str(refund["note"])[:255]
    return detail
