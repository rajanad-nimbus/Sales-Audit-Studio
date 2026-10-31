"""Input validation for governed transaction correction proposals."""

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException

from apps.ingest.common import SIGN, VALID_TYPES
from apps.totals.currency import validate_currency
from apps.transactions.detail import MAX_MONEY, cent_value, money


def validate_adjustment_values(adjustment_type: str, values: dict) -> None:
    """Ensure correction fields match their type before financial calculations."""
    try:
        if adjustment_type == "MissingTransaction":
            validate_missing_transaction(values)
            return
        allowed_fields = {
            "Amount": {"amount_delta", "corrected_amount"},
            "Tax": {"tax_delta"},
            "Tender": {"tender_type"},
            "Reference": {"reference"},
            "Item": {"item"},
        }
        allowed = allowed_fields.get(adjustment_type)
        if not isinstance(values, dict) or not allowed or len(values) != 1 or not set(values) <= allowed:
            raise ValueError("provide exactly one field appropriate to the adjustment type")
        field, value = next(iter(values.items()))
        if adjustment_type in {"Amount", "Tax"}:
            amount = cent_value(value, field)
            if abs(amount) > MAX_MONEY:
                raise ValueError(f"{field} must fit supported monetary precision")
        elif not isinstance(value, str) or not value.strip() or len(value) > 255:
            raise ValueError(f"{field} must be a nonempty string of at most 255 characters")
    except (ValueError, TypeError, InvalidOperation) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid adjustment values: {exc}") from exc


def validate_missing_transaction(values: dict) -> tuple[Decimal, str, str, datetime, str]:
    """Validate fields needed to safely create an approved missing transaction."""
    try:
        validate_missing_transaction_fields(values)
        amount = money(values["amount"], "amount", allow_zero=False)
        business_date = parse_business_date(values["business_date"])
        validate_store_id(values["store_id"])
        transaction_type = validate_transaction_type(values["transaction_type"])
        missing_transaction_source(values)
        event_at = parse_event_timestamp(values.get("event_timestamp"), business_date)
        settlement_date = parse_settlement_date(values.get("settlement_date"), business_date)
        validate_currency(values.get("currency") or "USD")
        validate_quantity(values.get("quantity"))
        validate_optional_text_fields(values)
        return amount, transaction_type, business_date, event_at, settlement_date
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid missing transaction: {exc}") from exc


def validate_missing_transaction_fields(values: dict) -> None:
    allowed = {
        "amount", "business_date", "store_id", "transaction_type", "event_timestamp",
        "settlement_date", "currency", "quantity", "register_id", "channel_id", "tender_type",
        "payment_reference", "settlement_reference", "source_system",
    }
    if not isinstance(values, dict) or set(values) - allowed:
        raise ValueError("unsupported missing transaction fields")


def parse_business_date(value: object) -> str:
    business_date = str(value)
    if date.fromisoformat(business_date).isoformat() != business_date:
        raise ValueError("business_date must use YYYY-MM-DD")
    return business_date


def validate_store_id(value: object) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 50 or value != value.strip():
        raise ValueError("store_id must contain 1 to 50 characters without surrounding whitespace")


def validate_transaction_type(value: object) -> str:
    if not isinstance(value, str) or value not in SIGN:
        raise ValueError("unsupported transaction_type")
    return value


def parse_event_timestamp(value: object, business_date: str) -> datetime:
    raw_value = value or f"{business_date}T00:00:00+00:00"
    event_at = datetime.fromisoformat(str(raw_value).replace("Z", "+00:00"))
    return event_at.replace(tzinfo=timezone.utc) if event_at.tzinfo is None else event_at


def parse_settlement_date(value: object, business_date: str) -> str:
    settlement_date = str(value or business_date)
    if date.fromisoformat(settlement_date).isoformat() != settlement_date:
        raise ValueError("settlement_date must use YYYY-MM-DD")
    return settlement_date


def validate_quantity(value: object) -> None:
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, int) or not -(2**31) <= value < 2**31
    ):
        raise ValueError("quantity must be a 32-bit integer")


def validate_optional_text_fields(values: dict) -> None:
    for name, max_length in (
        ("register_id", 50), ("channel_id", 50), ("tender_type", 50),
        ("payment_reference", 255), ("settlement_reference", 255),
    ):
        value = values.get(name)
        if value is not None and (not isinstance(value, str) or len(value) > max_length):
            raise ValueError(f"{name} must be a string of at most {max_length} characters")


def missing_transaction_source(values: dict) -> str:
    """Resolve an unambiguous source, requiring an explicit source for shared types."""
    transaction_type = values["transaction_type"]
    candidates = [source for source, types in VALID_TYPES.items() if transaction_type in types]
    source = values.get("source_system")
    if source is None and len(candidates) == 1:
        return candidates[0]
    if not isinstance(source, str) or source not in candidates:
        raise ValueError("source_system must identify a valid source for the transaction type")
    return source
