from decimal import Decimal

import pytest
from apps.ingest.pos_formats.standard import client_tender_code
from apps.transactions.detail import CLIENT_TENDER_CODES, parse_tender


def test_rms_tenders_map_to_client_codes_and_keep_the_generic_type():
    cases = [
        ("CCARD", "3000", "10", "VISA"),
        ("CCARD", "3020", "10", "AMEX"),
        ("DCARD", "", "10", "MCVISADBT"),
        ("CHECK", "2000", "10", "CHECK"),
        ("CASH", "1000", "10", "CASH"),
        ("VOUCH", "4040", "10", "GIFTCRDU"),
        ("VOUCH", "4040", "-10", "MCRISSUE"),
        ("OTHERS", "9012", "10", "AMAZON"),
    ]
    for group, type_id, amount, expected in cases:
        code = client_tender_code(group, type_id, Decimal(amount))
        assert code == expected and code in CLIENT_TENDER_CODES
    assert client_tender_code("CCARD", "3999", Decimal("5")) is None
    assert client_tender_code("SCRED", "", Decimal("5")) is None


def test_a_tender_keeps_its_generic_type_and_validates_the_client_code():
    tender = {"tender_type": "Card", "amount": "10", "client_tender_code": "VISA"}
    parsed = parse_tender(tender, 1, 1)
    assert parsed["tender_type"] == "Card" and parsed["client_tender_code"] == "VISA"
    assert parse_tender({"tender_type": "Card", "amount": "10"}, 1, 1)["client_tender_code"] is None
    with pytest.raises(ValueError, match="client_tender_code"):
        parse_tender({**tender, "client_tender_code": "BOGUS"}, 1, 1)


def test_shopify_gateways_that_name_a_client_tender_carry_its_code():
    from apps.ingest.shopify.detail import client_tender_code as shopify_code
    from apps.ingest.shopify.detail import order_tenders

    assert shopify_code("PayPal Express") == "PAYPAL"
    assert shopify_code("shopify_payments") is None
    tenders = order_tenders({}, [{"kind": "sale", "gateway": "paypal", "amount": "5", "id": 1}])
    assert tenders[0]["tender_type"] == "Other" and tenders[0]["client_tender_code"] == "PAYPAL"
