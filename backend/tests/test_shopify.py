"""Unit tests for the Shopify adapter helpers and ingest validation (no network or DB)."""
import base64
import hashlib
import hmac
from decimal import Decimal

import pytest

import shopify_adapter as sa
import shopify_ingest as si


def test_next_page_info_parses_link_header():
    link = ('<https://s/admin/api/2024-01/orders.json?limit=250&page_info=prev1>; rel="previous", '
            '<https://s/admin/api/2024-01/orders.json?limit=250&page_info=next2>; rel="next"')
    assert sa._next_page_info(link) == "next2"
    assert sa._next_page_info("") is None


def test_refund_amount_prefers_transactions():
    refund = {"transactions": [{"kind": "refund", "status": "success", "amount": "12.50"},
                               {"kind": "refund", "status": "failure", "amount": "99"}],
              "refund_line_items": [{"quantity": 1, "line_item": {"price": "50"}}]}
    assert sa.refund_amount(refund) == Decimal("12.50")


def test_refund_amount_falls_back_to_line_items():
    refund = {"refund_line_items": [{"quantity": 2, "line_item": {"price": "5.00"}}]}
    assert sa.refund_amount(refund) == Decimal("10.00")


def test_normalize_order_handles_null_customer():
    rec = sa.normalize_order({"id": 1, "created_at": "2024-01-15T10:00:00Z", "total_price": "20.00",
                              "currency": "USD", "customer": None}, "S1")
    assert rec["payment_reference"] == "1" and rec["customer_id"] is None
    assert si.parse(rec)["amount"] == Decimal("20.00")


def test_normalize_refund_links_to_order():
    rec = sa.normalize_refund({"id": 9, "created_at": "2024-01-16T10:00:00Z",
                               "transactions": [{"kind": "refund", "amount": "5", "currency": "EUR"}]}, 1, None, "S1")
    assert rec["payment_reference"] == rec["order_id"] == "1" and rec["currency"] == "EUR"
    assert si.parse(rec)["amount"] == Decimal("5.00")


def test_parse_quarantine_cases():
    base = {"source_record_id": "x", "event_time": "2024-01-15T10:00:00Z", "amount": "1", "store_id": "S",
            "payment_reference": "1", "record_type": "Order"}
    with pytest.raises(ValueError, match="non-negative"):
        si.parse({**base, "amount": "-1"})
    with pytest.raises(ValueError, match="financial_status"):
        si.parse({**base, "financial_status": "bogus"})
    with pytest.raises(ValueError, match="ISO-8601"):
        si.parse({**base, "event_time": "yesterday"})
    with pytest.raises(ValueError, match="order_id"):
        si.parse({**base, "record_type": "Refund"})


def test_webhook_signature():
    body = b'{"id": 1}'
    good = base64.b64encode(hmac.new(b"secret", body, hashlib.sha256).digest()).decode()
    assert sa.verify_webhook_signature("secret", body, good)
    assert not sa.verify_webhook_signature("secret", body, "bad")
    assert not sa.verify_webhook_signature(None, body, good)


def test_identity_payload_ignores_mutable_fields():
    a = sa.normalize_order({"id": 1, "created_at": "2024-01-15T10:00:00Z", "total_price": "20.00",
                            "financial_status": "paid"}, "S1")
    b = {**a, "financial_status": "refunded", "fulfillment_status": "shipped", "note": "changed"}
    assert si.identity_payload(a) == si.identity_payload(b)
    assert si.identity_payload(a) != si.identity_payload({**a, "amount": "21.00"})
