"""Shopify-specific ingestion parsing and validation."""
from datetime import UTC, datetime
from decimal import InvalidOperation

from apps.transactions import detail as transaction_detail
from apps.transactions.detail import MAX_MONEY

SHOPIFY_VALID_RECORD_TYPES = {"Order", "Refund"}

SHOPIFY_FINANCIAL_STATUSES = {
    "authorized", "pending", "paid", "partially_paid", "refunded", "voided", "partially_refunded"
}

SHOPIFY_FULFILLMENT_STATUSES = {"unshipped", "partial", "shipped", "delivered", "restocked"}


# Fields that never change once Shopify creates the record. Status, notes and fulfillment move over
# time, so they are excluded from the dedup hash; otherwise every re-sync of an updated order would
# be quarantined as a "conflicting resubmission".
IMMUTABLE_FIELDS = (
    "source_record_id", "record_type", "store_id", "event_time", "amount", "currency",
    "payment_reference", "order_id", "refund_id",
)


def identity_payload(rec: dict) -> dict:
    """The subset of a record used for duplicate/conflict detection."""
    return {k: rec.get(k) for k in IMMUTABLE_FIELDS}


def tax_detail(rec: dict, sign: int) -> dict | None:
    """The tax Shopify reported on an order or refund, as a transaction detail block (negative for a refund)."""
    taxes = rec.get("taxes")
    if taxes is None or taxes == []:
        return None
    try:
        return transaction_detail.extract({"taxes": taxes}, sign)
    except (ValueError, InvalidOperation, OverflowError) as exc:
        raise ValueError(f"invalid transaction detail: {exc}") from exc


def parse_shopify_order(rec: dict) -> dict:
    """Validate and normalize a Shopify order record."""
    required = ["source_record_id", "event_time", "amount", "store_id", "payment_reference"]
    missing = [k for k in required if rec.get(k) in (None, "")]
    if missing:
        raise ValueError(f"missing fields: {', '.join(missing)}")

    if rec.get("record_type") != "Order":
        raise ValueError("record_type must be 'Order'")

    amount = transaction_detail.cent_value(rec["amount"], "amount")

    if amount < 0:
        raise ValueError("amount must be non-negative")
    if amount > MAX_MONEY:
        raise ValueError("amount exceeds supported precision")

    try:
        ts = datetime.fromisoformat(str(rec["event_time"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("event_time is not ISO-8601") from exc

    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)

    financial_status = rec.get("financial_status", "pending")
    if not isinstance(financial_status, str):
        raise ValueError("financial_status must be a string")
    financial_status = financial_status.lower()
    if financial_status not in SHOPIFY_FINANCIAL_STATUSES:
        raise ValueError(f"invalid financial_status: {financial_status}")

    return {
        "amount": amount,
        "ts": ts,
        "financial_status": financial_status,
        "channel_id": "ecommerce",
        "detail": tax_detail(rec, 1),
    }


def parse_shopify_refund(rec: dict) -> dict:
    """Validate and normalize a Shopify refund record."""
    required = ["source_record_id", "event_time", "amount", "store_id", "payment_reference", "order_id"]
    missing = [k for k in required if rec.get(k) in (None, "")]
    if missing:
        raise ValueError(f"missing fields: {', '.join(missing)}")

    if rec.get("record_type") != "Refund":
        raise ValueError("record_type must be 'Refund'")

    amount = transaction_detail.cent_value(rec["amount"], "amount")

    if amount < 0:
        raise ValueError("amount must be non-negative")
    if amount > MAX_MONEY:
        raise ValueError("amount exceeds supported precision")

    try:
        ts = datetime.fromisoformat(str(rec["event_time"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("event_time is not ISO-8601") from exc

    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)

    return {
        "amount": amount,
        "ts": ts,
        "order_id": str(rec["order_id"]),
        "refund_id": str(rec.get("refund_id", "")),
        "channel_id": "ecommerce",
        "detail": tax_detail(rec, -1),
    }


def parse(rec: dict) -> dict:
    """Route to appropriate parser based on record_type."""
    record_type = rec.get("record_type")

    if record_type == "Order":
        return parse_shopify_order(rec)
    elif record_type == "Refund":
        return parse_shopify_refund(rec)
    else:
        raise ValueError(f"unknown record_type: {record_type}")
