from decimal import Decimal

import pytest
from apps.ingest.shopify import adapter as sa
from apps.transactions import detail as transaction_detail


def item(title, price, qty=1, **extra):
    return {
        "title": title,
        "price": price,
        "quantity": qty,
        "taxable": True,
        "sku": title[:3].upper(),
        **extra,
    }


def order(items, total, **extra):
    return {
        "id": 1,
        "created_at": "2026-10-01T10:00:00Z",
        "financial_status": "paid",
        "payment_gateway_names": ["manual"],
        "total_price": total,
        "line_items": items,
        **extra,
    }


def checked(record, sign=1):
    detail = transaction_detail.extract(record, sign)
    return detail, transaction_detail.check(detail, Decimal(record["amount"]) * sign)


def test_flat_tax_is_spread_over_taxable_lines_and_balances():
    rec = sa.normalize_order(
        order(
            [item("Shirt", "40.00"), item("Food", "30.00", taxable=False)],
            "73.20",
            tax_lines=[{"title": "Sales tax", "rate": 0.08, "price": "3.20"}],
        ),
        "S1",
    )
    detail, result = checked(rec)
    assert result["status"] == "Balanced"
    assert [line["tax_amount"] for line in detail["lines"]] == [Decimal("3.20"), Decimal("0.00")]


def test_multi_jurisdiction_tax_balances():
    rec = sa.normalize_order(
        order(
            [item("Jeans", "80.00")],
            "86.00",
            tax_lines=[
                {"title": "State tax", "rate": 0.06, "price": "4.80"},
                {"title": "County tax", "rate": 0.01, "price": "0.80"},
                {"title": "City tax", "rate": 0.005, "price": "0.40"},
            ],
        ),
        "S1",
    )
    _, result = checked(rec)
    assert result["status"] == "Balanced" and result["tax"] == Decimal("6.00")


def test_tax_inclusive_order_balances():
    rec = sa.normalize_order(
        order(
            [item("Jeans", "80.00")],
            "80.00",
            taxes_included=True,
            tax_lines=[{"title": "VAT", "rate": 0.13, "price": "9.20"}],
        ),
        "S1",
    )
    detail, result = checked(rec)
    assert result["status"] == "Balanced"
    assert detail["lines"][0]["gross_amount"] == Decimal("70.80")


def test_shipping_is_its_own_line_and_taxable_shipping_balances():
    tax = lambda amount: [{"title": "Tax", "rate": 0.08, "price": amount}]  # noqa: E731
    rec = sa.normalize_order(
        order(
            [item("Shirt", "40.00", tax_lines=tax("3.20"))],
            "51.84",
            shipping_lines=[{"title": "Standard", "price": "8.00", "tax_lines": tax("0.64")}],
        ),
        "S1",
    )
    detail, result = checked(rec)
    assert [line["item_code"] for line in detail["lines"]] == ["SHI", "SHIPPING"]
    assert result["status"] == "Balanced"


def test_discount_applications_become_typed_discounts_and_free_shipping_balances():
    allocations = lambda amount, index: [{"amount": amount, "discount_application_index": index}]  # noqa: E731
    rec = sa.normalize_order(
        order(
            [item("Jeans", "80.00", discount_allocations=allocations("8.00", 0))],
            "79.20",
            shipping_lines=[
                {
                    "title": "Standard",
                    "price": "9.00",
                    "discount_allocations": allocations("9.00", 1),
                }
            ],
            discount_applications=[
                {"type": "automatic", "title": "Spring 10%"},
                {"type": "discount_code", "code": "FREESHIP", "title": "FREESHIP"},
            ],
            tax_lines=[{"title": "Sales tax", "rate": 0.1, "price": "7.20"}],
        ),
        "S1",
    )
    detail, result = checked(rec)
    assert [(d["kind"], d["code"], d["amount"]) for d in detail["discounts"]] == [
        ("Promotion", "Spring 10%", Decimal("8.00")),
        ("Coupon", "FREESHIP", Decimal("9.00")),
    ]
    assert result["status"] == "Balanced"


def test_split_tenders_from_transactions_map_types_and_balance():
    transactions = [
        {"id": 11, "kind": "sale", "status": "success", "gateway": "manual", "amount": "32.40"},
        {"id": 12, "kind": "sale", "status": "success", "gateway": "gift_card", "amount": "32.40"},
        {
            "id": 13,
            "kind": "authorization",
            "status": "success",
            "gateway": "manual",
            "amount": "9.00",
        },
    ]
    rec = sa.normalize_order(
        order(
            [item("Shirt", "60.00")],
            "64.80",
            tax_lines=[{"title": "Tax", "rate": 0.08, "price": "4.80"}],
        ),
        "S1",
        transactions,
    )
    detail, result = checked(rec)
    assert [t["tender_type"] for t in detail["tenders"]] == ["Card", "GiftCard"]
    assert result["status"] == "Balanced"


def test_overpaid_tender_is_flagged_unbalanced():
    transactions = [
        {"id": 1, "kind": "sale", "status": "success", "gateway": "cash", "amount": "32.00"}
    ]
    rec = sa.normalize_order(
        order(
            [item("Hat", "25.00")],
            "27.00",
            tax_lines=[{"title": "Tax", "rate": 0.08, "price": "2.00"}],
        ),
        "S1",
        transactions,
    )
    _, result = checked(rec)
    assert result["status"] == "Unbalanced" and result["tender_variance"] == Decimal("5.00")


@pytest.mark.parametrize("status", ["pending", "authorized", "voided"])
def test_unsettled_orders_carry_no_tender(status):
    rec = sa.normalize_order(order([item("Hat", "25.00")], "25.00", financial_status=status), "S1")
    assert rec["tenders"] == []


def test_missing_sku_is_visible_as_no_sku():
    rec = sa.normalize_order(
        order([{"title": "Mystery", "price": "19.99", "quantity": 1, "sku": ""}], "19.99"), "S1"
    )
    assert rec["lines"][0]["item_code"] == "NO-SKU"


def test_line_refund_balances_and_links_back_to_the_order():
    refund = {
        "id": 9,
        "created_at": "2026-10-02T10:00:00Z",
        "note": "Too small",
        "transactions": [
            {"id": 5, "kind": "refund", "status": "success", "gateway": "manual", "amount": "86.40"}
        ],
        "refund_line_items": [
            {
                "quantity": 1,
                "subtotal": "80.00",
                "total_tax": "6.40",
                "line_item": item("Jeans", "80.00"),
            }
        ],
    }
    rec = sa.normalize_refund(refund, 1, "USD", "S1")
    detail, result = checked(rec, sign=-1)
    assert result["status"] == "Balanced"
    assert detail["original_reference"] == "1" and detail["return_reason"] == "Too small"


def test_refund_with_restocking_fee_keeps_tender_but_drops_unexplained_lines():
    refund = {
        "id": 9,
        "created_at": "2026-10-02T10:00:00Z",
        "transactions": [
            {"kind": "refund", "status": "success", "gateway": "manual", "amount": "147.00"}
        ],
        "refund_line_items": [
            {
                "quantity": 1,
                "subtotal": "150.00",
                "total_tax": "12.00",
                "line_item": item("Jacket", "150.00"),
            }
        ],
    }
    rec = sa.normalize_refund(refund, 1, "USD", "S1")
    assert rec["lines"] == [] and rec["tenders"][0]["amount"] == "147.00"


def test_tax_lines_that_were_never_charged_do_not_unbalance_the_order():
    rec = sa.normalize_order(
        order(
            [item("Jeans", "80.00")],
            "80.00",
            total_tax="0.00",
            tax_lines=[{"title": "Sales tax", "rate": 0.08, "price": "6.40"}],
        ),
        "S1",
    )
    detail, result = checked(rec)
    assert result["status"] == "Balanced" and detail["lines"][0]["tax_amount"] == Decimal("0.00")


def test_charged_total_tax_is_the_amount_spread_over_the_lines():
    rec = sa.normalize_order(
        order(
            [item("Jeans", "80.00")],
            "86.40",
            total_tax="6.40",
            tax_lines=[{"title": "Sales tax", "rate": 0.08, "price": "6.40"}],
        ),
        "S1",
    )
    assert checked(rec)[1]["status"] == "Balanced"


def test_line_level_tax_that_was_never_charged_is_dropped_to_the_charged_total():
    tax = [{"title": "Sales tax", "rate": 0.08, "price": "6.40"}]
    rec = sa.normalize_order(
        order([item("Jeans", "80.00", tax_lines=tax)], "80.00", total_tax="0.00", tax_lines=tax),
        "S1",
    )
    detail, result = checked(rec)
    assert result["status"] == "Balanced" and detail["lines"][0]["tax_amount"] == Decimal("0.00")


def test_line_level_tax_is_scaled_to_the_charged_total():
    lines = [item("Jeans", "80.00", tax_lines=[{"title": "Tax", "rate": 0.1, "price": "8.00"}]),
             item("Hat", "20.00", tax_lines=[{"title": "Tax", "rate": 0.1, "price": "2.00"}])]  # fmt: skip
    rec = sa.normalize_order(order(lines, "105.00", total_tax="5.00"), "S1")
    detail, result = checked(rec)
    assert result["status"] == "Balanced"
    assert [line["tax_amount"] for line in detail["lines"]] == [Decimal("4.00"), Decimal("1.00")]
