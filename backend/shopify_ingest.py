"""Shopify-specific ingestion parsing and validation."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

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


def parse_shopify_order(rec: dict) -> dict:
    """Validate and normalize a Shopify order record."""
    required = ["source_record_id", "event_time", "amount", "store_id", "payment_reference"]
    missing = [k for k in required if rec.get(k) in (None, "")]
    if missing:
        raise ValueError(f"missing fields: {', '.join(missing)}")

    if rec.get("record_type") != "Order":
        raise ValueError("record_type must be 'Order'")

    try:
        amount = Decimal(str(rec["amount"])).quantize(Decimal("0.01"))
    except (ValueError, InvalidOperation):
        raise ValueError("amount is not a valid decimal")

    if amount < 0:
        raise ValueError("amount must be non-negative")

    try:
        ts = datetime.fromisoformat(str(rec["event_time"]).replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("event_time is not ISO-8601")

    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    financial_status = rec.get("financial_status", "pending").lower()
    if financial_status not in SHOPIFY_FINANCIAL_STATUSES:
        raise ValueError(f"invalid financial_status: {financial_status}")

    return {
        "amount": amount,
        "ts": ts,
        "financial_status": financial_status,
        "channel_id": "ecommerce",
    }


def parse_shopify_refund(rec: dict) -> dict:
    """Validate and normalize a Shopify refund record."""
    required = ["source_record_id", "event_time", "amount", "store_id", "payment_reference", "order_id"]
    missing = [k for k in required if rec.get(k) in (None, "")]
    if missing:
        raise ValueError(f"missing fields: {', '.join(missing)}")

    if rec.get("record_type") != "Refund":
        raise ValueError("record_type must be 'Refund'")

    try:
        amount = Decimal(str(rec["amount"])).quantize(Decimal("0.01"))
    except (ValueError, InvalidOperation):
        raise ValueError("amount is not a valid decimal")

    if amount < 0:
        raise ValueError("amount must be non-negative")

    try:
        ts = datetime.fromisoformat(str(rec["event_time"]).replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("event_time is not ISO-8601")

    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    return {
        "amount": amount,
        "ts": ts,
        "order_id": str(rec["order_id"]),
        "refund_id": str(rec.get("refund_id", "")),
        "channel_id": "ecommerce",
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
