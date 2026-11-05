"""Shopify-specific ingestion parsing and validation."""

from datetime import UTC, datetime
from decimal import InvalidOperation

from apps.ingest.manifests import validate_business_date
from apps.transactions import detail as transaction_detail
from apps.transactions.detail import MAX_MONEY

SHOPIFY_VALID_RECORD_TYPES = {"Order", "Refund", "DayTotal"}

SHOPIFY_FINANCIAL_STATUSES = {
    "authorized",
    "pending",
    "paid",
    "partially_paid",
    "refunded",
    "voided",
    "partially_refunded",
}

SHOPIFY_FULFILLMENT_STATUSES = {"unshipped", "partial", "shipped", "delivered", "restocked"}


# Fields that never change once Shopify creates the record. Status, notes and fulfillment move over
# time, so they are excluded from the dedup hash; otherwise every re-sync of an updated order would
# be quarantined as a "conflicting resubmission".
IMMUTABLE_FIELDS = (
    "source_record_id",
    "record_type",
    "store_id",
    "event_time",
    "amount",
    "currency",
    "payment_reference",
    "order_id",
    "refund_id",
)


def identity_payload(rec: dict) -> dict:
    """The subset of a record used for duplicate/conflict detection.

    A day total has no mutable fields: all of it is the figure being declared, so all of it counts.
    """
    if rec.get("record_type") == "DayTotal":
        return rec
    return {k: rec.get(k) for k in IMMUTABLE_FIELDS}


DETAIL_FIELDS = ("lines", "discounts", "taxes", "tenders", "original_reference", "return_reason")


def record_detail(rec: dict, sign: int) -> dict | None:
    """Lines, discounts, tax and tenders Shopify reported, as a transaction detail block (negative for a refund)."""
    block = {k: rec[k] for k in DETAIL_FIELDS if rec.get(k) not in (None, [])}
    if not block:
        return None
    try:
        return transaction_detail.extract(block, sign)
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
        "detail": record_detail(rec, 1),
    }


def parse_shopify_refund(rec: dict) -> dict:
    """Validate and normalize a Shopify refund record."""
    required = [
        "source_record_id",
        "event_time",
        "amount",
        "store_id",
        "payment_reference",
        "order_id",
    ]
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
        "detail": record_detail(rec, -1),
    }


def _count(rec: dict, field: str) -> int:
    value = rec[field]
    if isinstance(value, bool) or not (
        isinstance(value, int) or (isinstance(value, str) and value.isdecimal())
    ):
        raise ValueError(f"{field} must be a non-negative integer")
    count = int(value)
    if not 0 <= count <= 2**63 - 1:
        raise ValueError(f"{field} must be a non-negative 64-bit integer")
    return count


def _total(rec: dict, field: str):
    total = transaction_detail.cent_value(rec[field], field)
    if total < 0:
        raise ValueError(f"{field} must be non-negative")
    if total > MAX_MONEY:
        raise ValueError(f"{field} exceeds supported precision")
    return total


def parse_shopify_day_total(rec: dict) -> dict:
    """Validate what Shopify reports for one store's business day: orders, and optionally refunds.

    Refund figures are optional because the connector cannot see every refund issued on a day (a refund
    of an older order arrives only by webhook); a Shopify finance export can supply them.
    """
    required = ["source_record_id", "store_id", "business_date", "order_count", "order_total"]
    missing = [k for k in required if rec.get(k) in (None, "")]
    if missing:
        raise ValueError(f"missing fields: {', '.join(missing)}")
    has_refunds = [rec.get(k) not in (None, "") for k in ("refund_count", "refund_total")]
    if any(has_refunds) and not all(has_refunds):
        raise ValueError("refund_count and refund_total must be supplied together")
    business_date = validate_business_date(rec["business_date"])
    try:
        values = {
            "order_count": _count(rec, "order_count"),
            "order_total": _total(rec, "order_total"),
            "refund_count": _count(rec, "refund_count") if all(has_refunds) else None,
            "refund_total": _total(rec, "refund_total") if all(has_refunds) else None,
        }
    except (TypeError, InvalidOperation, OverflowError):
        raise ValueError("invalid day total values") from None
    return {
        "manifest": True,
        **values,
        "ts": datetime.fromisoformat(f"{business_date}T00:00:00+00:00"),
    }


def parse(rec: dict) -> dict:
    """Route to appropriate parser based on record_type."""
    record_type = rec.get("record_type")

    if record_type == "Order":
        return parse_shopify_order(rec)
    elif record_type == "Refund":
        return parse_shopify_refund(rec)
    elif record_type == "DayTotal":
        return parse_shopify_day_total(rec)
    else:
        raise ValueError(f"unknown record_type: {record_type}")
