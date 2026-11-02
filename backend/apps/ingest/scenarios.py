"""A matrix of POS sale and return combinations with the audit errors each one should raise.

Every scenario is a small feed: transaction type x tender x tax shape x discount x return behavior, plus
deliberately broken variants. `expect` is the set of line-level audit errors the audit must raise, so the matrix is
both demo data (`matrix_feed`) and an executable specification (see tests/test_scenario_matrix.py).
"""

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
DAY = "2026-10-01"
STORE = "M01"
AUDIT_ERRORS = {
    "TRAN_OUT_BAL",
    "DISC_REASON_REQ",
    "SKU_NOT_FOUND",
    "TAX_MISMATCH",
    "RETURN_OVER_SOLD",
}


@dataclass
class Scenario:
    name: str
    group: str
    pos: list[dict]
    processor: list[dict] = field(default_factory=list)
    expect: set[str] = field(default_factory=set)
    catalog: tuple[str, ...] = ()


def cents(value) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def line(code="SHIRT", qty=1, price="100.00", rate="0.13", tax_code="VAT", **extra) -> dict:
    gross = cents(Decimal(qty) * Decimal(price))
    return {
        "item_code": code,
        "quantity": str(qty),
        "unit_price": price,
        "gross_amount": str(
            gross
        ),  # a POS sends the rounded line total, which matters for fractional quantities
        "tax_code": tax_code if Decimal(rate) else None,
        "tax_rate": rate,
        "tax_amount": str(cents(gross * Decimal(rate))),
        **extra,
    }


def discount(kind, amount, code=None, description="Seasonal offer", line_no=None) -> dict:
    return {
        "kind": kind,
        "code": code,
        "description": description,
        "amount": amount,
        "line_no": line_no,
    }


def total_of(lines, discounts=()) -> Decimal:
    gross = sum(
        (cents(Decimal(x["quantity"]) * Decimal(x["unit_price"])) for x in lines), Decimal("0")
    )
    tax = sum((Decimal(x["tax_amount"]) for x in lines), Decimal("0"))
    return gross - sum((Decimal(d["amount"]) for d in discounts), Decimal("0")) + tax


def split(total: Decimal, tenders) -> list[dict]:
    """Share `total` between tender types (type, share); the last one takes the rounding remainder."""
    out, paid = [], Decimal("0")
    for n, (kind, share) in enumerate(tenders):
        amount = total - paid if n == len(tenders) - 1 else cents(total * Decimal(str(share)))
        out.append({"tender_type": kind, "amount": str(amount)})
        paid += amount
    return out


class Feed:
    """Builds the POS records of one scenario and the processor records its card money needs."""

    def __init__(self, name: str):
        self.name, self.pos, self.processor = name, [], []

    def record(
        self,
        kind,
        lines,
        tenders=(("Cash", 1),),
        discounts=(),
        original=None,
        hour=10,
        day=DAY,
        currency="USD",
        **extra,
    ):
        n = len(self.pos) + 1
        total = extra.pop("amount", None) or total_of(lines, discounts)
        built = split(total, tenders) if not isinstance(tenders, list) else tenders
        base = {
            "event_time": f"{day}T{hour:02d}:00:00+00:00",
            "store_id": STORE,
            "currency": currency,
            "payment_reference": f"{self.name}-PAY-{n}",
        }
        self.pos.append(
            {
                **base,
                "source_record_id": f"{self.name}-POS-{n}",
                "record_type": kind,
                "amount": str(total),
                "register_id": "R1",
                "lines": lines,
                "discounts": list(discounts),
                "tenders": built,
                **({"original_reference": original} if original else {}),
                **extra,
            }
        )
        card = sum(
            (Decimal(t["amount"]) for t in built if t["tender_type"] == "Card"), Decimal("0")
        )
        if card:  # the processor only sees the card part of a payment
            self.processor.append(
                {
                    **base,
                    "source_record_id": f"{self.name}-PRC-{n}",
                    "record_type": "Capture" if kind == "Sale" else "Refund",
                    "amount": str(card),
                    "settled_on": day,
                    "settlement_reference": f"{self.name}-SET",
                }
            )
        return self.pos[-1]["payment_reference"]

    def done(self, group, expect=(), catalog=()) -> Scenario:
        return Scenario(self.name, group, self.pos, self.processor, set(expect), tuple(catalog))


def sale(name, group, lines, tenders=(("Cash", 1),), discounts=(), expect=(), catalog=(), **extra):
    feed = Feed(name)
    feed.record("Sale", lines, tenders, discounts, **extra)
    return feed.done(group, expect, catalog)


TENDERS = {
    "cash": (("Cash", 1),),
    "card": (("Card", 1),),
    "voucher": (("Voucher", 1),),
    "giftcard": (("GiftCard", 1),),
    "storecredit": (("StoreCredit", 1),),
    "other": (("Other", 1),),
    "split cash+card": (("Cash", 0.4), ("Card", 0.6)),
    "split cash+giftcard": (("Cash", 0.5), ("GiftCard", 0.5)),
}
TAXES = {
    "zero rate": [line(rate="0", tax_code=None), line("PEN", 3, "2.50", rate="0", tax_code=None)],
    "flat": [line(), line("PEN", 3, "2.50")],
    "two rates": [
        line(rate="0.13", tax_code="VAT13"),
        line("BOOK", 2, "20.00", rate="0.05", tax_code="VAT5"),
    ],
    "exempt line": [line(), line("FOOD", 2, "7.50", rate="0", tax_code=None)],
}


def sale_matrix() -> list[Scenario]:
    return [
        sale(f"sale {t} {x}", "sale", lines, tenders)
        for t, tenders in TENDERS.items()
        for x, lines in TAXES.items()
    ]


def discount_scenarios() -> list[Scenario]:
    lines = [line(), line("PEN", 3, "2.50")]
    return [
        sale(
            "discount promotion",
            "discount",
            lines,
            discounts=[discount("Promotion", "10.00", "PROMO10")],
        ),
        sale("discount coupon", "discount", lines, discounts=[discount("Coupon", "5.00", "CPN5")]),
        sale(
            "discount voucher",
            "discount",
            lines,
            discounts=[discount("Voucher", "20.00", "VCH-20")],
        ),
        sale(
            "discount manual with reason", "discount", lines, discounts=[discount("Manual", "8.00")]
        ),
        sale(
            "discount line level",
            "discount",
            lines,
            discounts=[discount("Promotion", "10.00", "PROMO10", line_no=1)],
        ),
        sale(
            "discount stacked",
            "discount",
            lines,
            discounts=[
                discount("Promotion", "10.00", "PROMO10"),
                discount("Coupon", "5.00", "CPN5"),
            ],
        ),
        sale(
            "discount manual without reason",
            "discount",
            lines,
            discounts=[discount("Manual", "8.00", description="")],
            expect={"DISC_REASON_REQ"},
        ),
    ]


def return_scenarios() -> list[Scenario]:
    out = []

    def sold(name, lines, tenders=(("Cash", 1),)):
        feed = Feed(name)
        ref = feed.record("Sale", lines, tenders)
        return feed, ref

    pair = [line("SHIRT", 1, "100.00"), line("JEANS", 1, "80.00")]
    feed, ref = sold("return full", pair)
    feed.record("Return", pair, original=ref, hour=14, return_reason="Changed mind")
    out.append(feed.done("return"))

    feed, ref = sold("return one line", pair)
    feed.record("Return", [pair[1]], original=ref, hour=14, return_reason="Wrong size")
    out.append(feed.done("return"))

    feed, ref = sold("return partial quantity", [line("SOCK", 4, "10.00")])
    feed.record("Return", [line("SOCK", 2, "10.00")], original=ref, hour=14)
    out.append(feed.done("return"))

    feed, ref = sold("return to another tender", pair, (("Card", 1),))
    feed.record("Return", pair, tenders=(("StoreCredit", 1),), original=ref, hour=14)
    out.append(feed.done("return"))

    feed, ref = sold("return card refund", pair, (("Card", 1),))
    feed.record("Return", pair, tenders=(("Card", 1),), original=ref, hour=14)
    out.append(feed.done("return"))

    feed, ref = sold("return in two steps", pair)
    feed.record("Return", [pair[0]], original=ref, hour=12)
    feed.record("Return", [pair[1]], original=ref, hour=14)
    out.append(feed.done("return"))

    feed, ref = sold("return more than sold", pair)
    feed.record("Return", [line("SHIRT", 2, "100.00")], original=ref, hour=14)
    out.append(feed.done("return", {"RETURN_OVER_SOLD"}))

    feed, ref = sold("return item not on the sale", pair)
    feed.record("Return", [line("COAT", 1, "150.00")], original=ref, hour=14)
    out.append(feed.done("return", {"RETURN_OVER_SOLD"}))

    feed, ref = sold("return twice the same item", pair)
    feed.record("Return", [pair[0]], original=ref, hour=12)
    feed.record("Return", [pair[0]], original=ref, hour=14)
    out.append(feed.done("return", {"RETURN_OVER_SOLD"}))
    return out


def error_scenarios() -> list[Scenario]:
    lines = [line(), line("PEN", 3, "2.50")]
    total = total_of(lines)
    return [
        sale(
            "error tender short",
            "error",
            lines,
            [{"tender_type": "Cash", "amount": str(total - 5)}],
            expect={"TRAN_OUT_BAL"},
        ),
        sale(
            "error tender over",
            "error",
            lines,
            [{"tender_type": "Cash", "amount": str(total + 5)}],
            expect={"TRAN_OUT_BAL"},
        ),
        sale(
            "error header above lines", "error", lines, amount=total + 12, expect={"TRAN_OUT_BAL"}
        ),
        sale(
            "error header below lines", "error", lines, amount=total - 12, expect={"TRAN_OUT_BAL"}
        ),
        sale("error item without a sku", "error", [line("NO-SKU")], expect={"SKU_NOT_FOUND"}),
        sale(
            "error item not in the item master",
            "error",
            [line("GHOST")],
            catalog=["SHIRT"],
            expect={"SKU_NOT_FOUND"},
        ),
        sale("error item in the item master", "error", [line("SHIRT")], catalog=["SHIRT"]),
        sale(
            "error tax too high",
            "error",
            [line(tax_amount="20.00")],
            [{"tender_type": "Cash", "amount": "120.00"}],
            expect={"TAX_MISMATCH"},
        ),
        sale(
            "error tax too low",
            "error",
            [line(tax_amount="7.00")],
            [{"tender_type": "Cash", "amount": "107.00"}],
            expect={"TAX_MISMATCH"},
        ),
        sale(
            "error out of balance hides the line rules",
            "error",
            [line("NO-SKU")],
            [{"tender_type": "Cash", "amount": "100.00"}],
            expect={"TRAN_OUT_BAL"},
        ),
    ]


def edge_scenarios() -> list[Scenario]:
    """Amounts, quantities, currencies and basket shapes at the edges."""
    basket = [
        line(f"ITEM{n:02d}", 1 + n % 3, f"{3 + n}.45", rate="0.0825", tax_code="TAX")
        for n in range(12)
    ]
    small = [
        line("PEN", 7, "0.99", rate="0.0825", tax_code="TAX"),
        line("CLIP", 9, "0.33", rate="0.0825", tax_code="TAX"),
    ]
    gift = [line("GIFTCARD", 2, "50.00", rate="0", tax_code=None)]
    return [
        sale("edge penny sale", "edge", [line("GUM", 1, "0.01", rate="0", tax_code=None)]),
        sale("edge very large sale", "edge", [line("PALLET", 1, "250000.00")], (("Card", 1),)),
        sale(
            "edge decimal quantity",
            "edge",
            [line("CHEESE", "1.5", "9.99", rate="0.05", tax_code="VAT5")],
        ),
        sale("edge rounding many small lines", "edge", small),
        sale("edge twelve line basket", "edge", basket, (("Cash", 0.3), ("Card", 0.7))),
        sale("edge gift card sale", "edge", gift, (("Card", 1),)),
        sale(
            "edge non-usd currency",
            "edge",
            [line("JEANS", 1, "80.00", rate="0.05", tax_code="GST")],
            currency="CAD",
        ),
    ]


def exchange_scenarios() -> list[Scenario]:
    """Returns after discounts, without a receipt, across days, and exchanges (a return plus a new sale)."""
    out = []
    shirt, jeans = line("SHIRT", 1, "100.00"), line("JEANS", 1, "80.00")
    cad = dict(rate="0.05", tax_code="GST")

    feed = Feed("exchange return of a discounted sale")
    ref = feed.record("Sale", [shirt, jeans], discounts=[discount("Promotion", "18.00", "PROMO10")])
    refund = cents(Decimal("100.00") * Decimal("0.9") * Decimal("1.13"))
    feed.record(
        "Return",
        [line("SHIRT", 1, "100.00", tax_amount="11.70")],
        discounts=[discount("Promotion", "10.00", "PROMO10")],
        original=ref,
        hour=14,
        amount=refund,
    )
    out.append(feed.done("exchange"))

    feed = Feed("exchange return without a receipt")
    feed.record("Return", [shirt], hour=14, return_reason="No receipt")
    out.append(feed.done("exchange"))

    feed = Feed("exchange return the next day")
    ref = feed.record("Sale", [shirt, jeans])
    feed.record("Return", [jeans], original=ref, day="2026-10-02", hour=11)
    out.append(feed.done("exchange"))

    feed = Feed("exchange even")
    ref = feed.record("Sale", [jeans])
    feed.record("Return", [jeans], tenders=(("StoreCredit", 1),), original=ref, hour=12)
    feed.record("Sale", [line("JEANS-L", 1, "80.00")], tenders=(("StoreCredit", 1),), hour=12)
    out.append(feed.done("exchange"))

    feed = Feed("exchange customer pays more")
    ref = feed.record("Sale", [shirt])
    feed.record("Return", [shirt], tenders=(("StoreCredit", 1),), original=ref, hour=12)
    feed.record(
        "Sale",
        [line("SHIRT-PREMIUM", 1, "150.00")],
        tenders=(("StoreCredit", 0.75), ("Cash", 0.25)),
        hour=12,
    )
    out.append(feed.done("exchange"))

    jacket = line("JACKET", 1, "150.00")
    feed = Feed("exchange customer refunded the difference")
    ref = feed.record("Sale", [jacket], (("Card", 1),))
    feed.record("Return", [jacket], (("Card", 1),), original=ref, hour=12)
    feed.record("Sale", [line("VEST", 1, "60.00")], (("Card", 1),), hour=12)
    out.append(feed.done("exchange"))

    feed = Feed("exchange non-usd return")
    ref = feed.record("Sale", [line("JEANS", 1, "80.00", **cad)], currency="CAD")
    feed.record("Return", [line("JEANS", 1, "80.00", **cad)], original=ref, hour=14, currency="CAD")
    out.append(feed.done("exchange"))

    gift = [line("GIFTCARD", 1, "50.00", rate="0", tax_code=None)]
    feed = Feed("exchange gift card return")
    ref = feed.record("Sale", gift)
    feed.record("Return", gift, original=ref, hour=14)
    out.append(feed.done("exchange"))
    return out


def scenarios() -> list[Scenario]:
    return (
        sale_matrix()
        + discount_scenarios()
        + return_scenarios()
        + edge_scenarios()
        + exchange_scenarios()
        + error_scenarios()
    )


def matrix_feed() -> dict[str, list[dict]]:
    """Every scenario's records as one POS and one Processor delivery, for loading demo data."""
    everything = scenarios()
    return {
        "POS": [r for s in everything for r in s.pos],
        "Processor": [r for s in everything for r in s.processor],
    }
