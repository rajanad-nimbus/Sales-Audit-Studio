"""Input validation for governed transaction correction proposals."""

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException

from apps.ingest.common import SIGN, VALID_TYPES
from apps.totals.currency import validate_currency
from apps.transactions import detail as transaction_detail
from apps.transactions.customer_attributes import normalize_customer_attributes
from apps.transactions.detail import MAX_MONEY, cent_value, money
from apps.transactions.references import REFERENCE_FIELDS, normalize_user_references


def validate_adjustment_values(adjustment_type: str, values: dict) -> None:
    """Ensure correction fields match their type before financial calculations."""
    try:
        if adjustment_type == "TaxDetail":
            fields = {"tax_id", "tax_code", "tax_name", "rate", "taxable_amount", "tax_amount"}
            before_fields = {f"before_{field}" for field in fields - {"tax_id"}}
            if (
                not isinstance(values, dict)
                or set(values) - fields - before_fields
                or not fields <= set(values)
            ):
                raise ValueError("provide a tax row and all corrected tax detail fields")
            if (
                not isinstance(values["tax_id"], str)
                or not values["tax_id"]
                or len(values["tax_id"]) > 36
            ):
                raise ValueError("tax_id must be a valid tax row identifier")
            for field, limit in (("tax_code", 30), ("tax_name", 100)):
                value = values[field]
                if (
                    not isinstance(value, str)
                    or not value.strip()
                    or value != value.strip()
                    or len(value) > limit
                ):
                    raise ValueError(f"{field} must contain 1 to {limit} characters")
            rate = Decimal(str(values["rate"]))
            if (
                not rate.is_finite()
                or rate < 0
                or rate > 1
                or max(0, -rate.as_tuple().exponent) > 4
            ):
                raise ValueError("rate must be between 0 and 1 with at most four decimal places")
            for field in ("taxable_amount", "tax_amount"):
                amount = cent_value(values[field], field)
                if abs(amount) > MAX_MONEY:
                    raise ValueError(f"{field} must fit supported monetary precision")
            present_before = before_fields & set(values)
            if present_before and present_before != before_fields:
                raise ValueError("before tax details must include every tax field")
            for field, limit in (("before_tax_code", 30), ("before_tax_name", 100)):
                value = values.get(field)
                if field in present_before and (not isinstance(value, str) or len(value) > limit):
                    raise ValueError(f"{field} must be text")
            if "before_rate" in present_before:
                before_rate = Decimal(str(values["before_rate"]))
                if (
                    not before_rate.is_finite()
                    or before_rate < 0
                    or before_rate > 1
                    or max(0, -before_rate.as_tuple().exponent) > 4
                ):
                    raise ValueError("before_rate is invalid")
            for field in ("before_taxable_amount", "before_tax_amount"):
                if field in present_before:
                    cent_value(values[field], field)
            return
        if adjustment_type == "TaxLine":
            fields = {"line_no", "tax_code", "rate", "tax_amount"}
            before_fields = {f"before_{field}" for field in fields - {"line_no"}}
            if (
                not isinstance(values, dict)
                or set(values) - fields - before_fields
                or not fields <= set(values)
            ):
                raise ValueError(
                    "provide an item line and its corrected tax code, rate, and amount"
                )
            if type(values["line_no"]) is not int or values["line_no"] < 1:
                raise ValueError("line_no must be a positive integer")
            tax_code = values["tax_code"]
            if tax_code is not None and (
                not isinstance(tax_code, str) or tax_code != tax_code.strip() or len(tax_code) > 30
            ):
                raise ValueError("tax_code must be blank or contain at most 30 characters")
            rate = Decimal(str(values["rate"]))
            if (
                not rate.is_finite()
                or rate < 0
                or rate > 1
                or max(0, -rate.as_tuple().exponent) > 4
            ):
                raise ValueError("rate must be between 0 and 1 with at most four decimal places")
            amount = cent_value(values["tax_amount"], "tax_amount")
            if abs(amount) > MAX_MONEY:
                raise ValueError("tax_amount must fit supported monetary precision")
            present_before = before_fields & set(values)
            if present_before and present_before != before_fields:
                raise ValueError("before line tax must include tax code, rate, and amount")
            if (
                "before_tax_code" in present_before
                and values["before_tax_code"] is not None
                and not isinstance(values["before_tax_code"], str)
            ):
                raise ValueError("before_tax_code must be text or null")
            if "before_rate" in present_before:
                before_rate = Decimal(str(values["before_rate"]))
                if (
                    not before_rate.is_finite()
                    or before_rate < 0
                    or before_rate > 1
                    or max(0, -before_rate.as_tuple().exponent) > 4
                ):
                    raise ValueError("before_rate is invalid")
            if "before_tax_amount" in present_before:
                cent_value(values["before_tax_amount"], "before_tax_amount")
            return
        if adjustment_type == "Discount":
            if not isinstance(values, dict):
                raise ValueError("discount correction must be an object")
            operation = values.get("operation", "update")
            if operation not in {"add", "update", "delete"}:
                raise ValueError("discount operation must be add, update, or delete")
            fields = {"kind", "code", "description", "line_no", "amount"}
            before_fields = {f"before_{field}" for field in fields}
            allowed = fields | before_fields | {"operation", "discount_id"}
            if set(values) - allowed:
                raise ValueError("discount correction contains unsupported fields")
            if operation == "delete":
                if fields & set(values):
                    raise ValueError(
                        "delete discount corrections cannot include replacement values"
                    )
            elif not fields <= set(values):
                raise ValueError(
                    "provide the corrected discount type, code, description, line and amount"
                )
            if operation in {"update", "delete"}:
                discount_id = values.get("discount_id")
                if not isinstance(discount_id, str) or not discount_id or len(discount_id) > 36:
                    raise ValueError("discount_id must identify a discount row")
            elif "discount_id" in values or before_fields & set(values):
                raise ValueError("new discount corrections cannot include an existing discount ID")
            if operation != "delete":
                if values["kind"] not in transaction_detail.DISCOUNT_KINDS:
                    raise ValueError(
                        f"kind must be one of {', '.join(transaction_detail.DISCOUNT_KINDS)}"
                    )
                for field, limit in (("code", 100), ("description", 255)):
                    value = values[field]
                    if value is not None and (
                        not isinstance(value, str) or value != value.strip() or len(value) > limit
                    ):
                        raise ValueError(f"{field} must be blank or at most {limit} characters")
                line_no = values["line_no"]
                if line_no is not None and (type(line_no) is not int or line_no < 1):
                    raise ValueError("line_no must be blank or a positive integer")
                amount = cent_value(values["amount"], "amount")
                if abs(amount) > MAX_MONEY:
                    raise ValueError("amount must fit supported monetary precision")
            present_before = before_fields & set(values)
            if present_before and present_before != before_fields:
                raise ValueError("before discount must include all recorded discount fields")
            if "before_amount" in present_before:
                cent_value(values["before_amount"], "before_amount")
            for field, limit in (
                ("before_kind", 20),
                ("before_code", 100),
                ("before_description", 255),
            ):
                value = values.get(field)
                if value is not None and (not isinstance(value, str) or len(value) > limit):
                    raise ValueError(f"{field} must be text or null")
            before_line_no = values.get("before_line_no")
            if before_line_no is not None and (
                type(before_line_no) is not int or before_line_no < 1
            ):
                raise ValueError("before_line_no must be a positive integer or null")
            return
        if adjustment_type == "MissingTransaction":
            validate_missing_transaction(values)
            missing_transaction_detail(values)
            normalize_user_references(values.get("user_references"))
            return
        if adjustment_type == "CustomerAttribute":
            allowed = {"attribute_type", "value", "before_value"}
            attribute_type = values.get("attribute_type") if isinstance(values, dict) else None
            value = values.get("value") if isinstance(values, dict) else None
            before_value = values.get("before_value") if isinstance(values, dict) else None
            if (
                not isinstance(values, dict)
                or set(values) - allowed
                or not isinstance(attribute_type, str)
                or not attribute_type.strip()
                or attribute_type != attribute_type.strip()
                or len(attribute_type) > 64
                or not isinstance(value, str)
                or len(value) > 255
                or (
                    before_value is not None
                    and (not isinstance(before_value, str) or len(before_value) > 255)
                )
            ):
                raise ValueError(
                    "provide a customer attribute name and a value of at most 255 characters"
                )
            return
        if adjustment_type == "Item":
            if (
                not isinstance(values, dict)
                or set(values) - {"item", "line_no", "before_item_code"}
                or not isinstance(values.get("item"), str)
                or not values["item"].strip()
                or len(values["item"]) > 100
            ):
                raise ValueError("item must be a nonempty item code of at most 100 characters")
            line_no = values.get("line_no")
            if line_no is not None and (type(line_no) is not int or line_no < 1):
                raise ValueError("line_no must be a positive integer")
            before_item = values.get("before_item_code")
            if before_item is not None and (
                not isinstance(before_item, str) or not before_item or len(before_item) > 100
            ):
                raise ValueError("before_item_code must be a valid item code")
            return
        if adjustment_type == "Customer":
            allowed = {
                "customer_id",
                "customer_identification_type",
                "before_customer_id",
                "before_customer_identification_type",
            }
            fields = {"customer_id", "customer_identification_type"}
            if not isinstance(values, dict) or set(values) - allowed or not (set(values) & fields):
                raise ValueError("provide a customer identifier, identification type, or both")
            for field, max_length in (("customer_id", 100), ("customer_identification_type", 50)):
                value = values.get(field)
                if value is not None and (
                    not isinstance(value, str)
                    or not value.strip()
                    or value != value.strip()
                    or len(value) > max_length
                ):
                    raise ValueError(
                        f"{field} must be nonempty text of at most {max_length} characters"
                    )
            for field, max_length in (
                ("before_customer_id", 100),
                ("before_customer_identification_type", 50),
            ):
                value = values.get(field)
                if value is not None and (not isinstance(value, str) or len(value) > max_length):
                    raise ValueError(f"{field} must be text")
            return
        if adjustment_type == "Employee":
            allowed = {
                "cashier_id",
                "salesperson_id",
                "before_cashier_id",
                "before_salesperson_id",
            }
            fields = {"cashier_id", "salesperson_id"}
            if not isinstance(values, dict) or set(values) - allowed or not (set(values) & fields):
                raise ValueError("provide a cashier ID, salesperson ID, or both")
            for field in fields:
                value = values.get(field)
                before_value = values.get(f"before_{field}")
                if value is not None and (
                    not isinstance(value, str)
                    or not value.strip()
                    or value != value.strip()
                    or len(value) > 100
                ):
                    raise ValueError(f"{field} must be nonempty text of at most 100 characters")
                if (
                    f"before_{field}" in values
                    and before_value is not None
                    and (not isinstance(before_value, str) or len(before_value) > 100)
                ):
                    raise ValueError(f"before_{field} must be text or null")
            return
        if adjustment_type == "UserReference":
            allowed = {"reference_field", "value", "before_value"}
            reference_field = values.get("reference_field") if isinstance(values, dict) else None
            value = values.get("value") if isinstance(values, dict) else None
            before_value = values.get("before_value") if isinstance(values, dict) else None
            if (
                not isinstance(values, dict)
                or set(values) - allowed
                or reference_field not in REFERENCE_FIELDS
                or not isinstance(value, str)
                or len(value) > 255
                or value != value.strip()
                or (
                    before_value is not None
                    and (not isinstance(before_value, str) or len(before_value) > 255)
                )
            ):
                raise ValueError(
                    "provide a valid reference slot and a value of at most 255 characters"
                )
            return
        if adjustment_type in {"Tender", "Reference"}:
            field = "tender_type" if adjustment_type == "Tender" else "reference"
            before_field = f"before_{field}"
            allowed_keys = {field, "tender_id", before_field, "updates_header", "tender_count"}
            if not isinstance(values, dict) or field not in values or set(values) - allowed_keys:
                raise ValueError(f"provide {field} and an optional tender_id")
            value = values[field]
            if not isinstance(value, str) or not value.strip() or len(value) > 255:
                raise ValueError(f"{field} must be a nonempty string of at most 255 characters")
            if adjustment_type == "Tender" and value not in transaction_detail.TENDER_TYPES:
                raise ValueError(
                    f"tender_type must be one of {', '.join(transaction_detail.TENDER_TYPES)}"
                )
            tender_id = values.get("tender_id")
            if tender_id is not None and (
                not isinstance(tender_id, str) or not tender_id or len(tender_id) > 36
            ):
                raise ValueError("tender_id must be a valid tender identifier")
            updates_header = values.get("updates_header")
            if updates_header is not None and not isinstance(updates_header, bool):
                raise ValueError("updates_header must be a boolean")
            tender_count = values.get("tender_count")
            if tender_count is not None and (
                type(tender_count) is not int or not 0 <= tender_count <= 10_000
            ):
                raise ValueError("tender_count must be a nonnegative integer")
            before_value = values.get(before_field)
            if before_value is not None and (
                not isinstance(before_value, str) or len(before_value) > 255
            ):
                raise ValueError(f"{before_field} must be text")
            return
        allowed_fields = {
            "Amount": {"amount_delta", "corrected_amount"},
            "Tax": {"tax_delta"},
        }
        allowed = allowed_fields.get(adjustment_type)
        if (
            not isinstance(values, dict)
            or not allowed
            or len(values) != 1
            or not set(values) <= allowed
        ):
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
        normalize_customer_attributes(values.get("customer_attributes"))
        return amount, transaction_type, business_date, event_at, settlement_date
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid missing transaction: {exc}") from exc


def validate_missing_transaction_fields(values: dict) -> None:
    allowed = {
        "amount",
        "business_date",
        "store_id",
        "transaction_type",
        "event_timestamp",
        "settlement_date",
        "currency",
        "quantity",
        "register_id",
        "transaction_number",
        "channel_id",
        "tender_type",
        "payment_reference",
        "settlement_reference",
        "source_system",
        "lines",
        "discounts",
        "taxes",
        "tenders",
        "original_reference",
        "return_reason",
        "customer_id",
        "customer_identification_type",
        "customer_attributes",
        "cashier_id",
        "salesperson_id",
        "user_references",
    }
    if not isinstance(values, dict) or set(values) - allowed:
        raise ValueError("unsupported missing transaction fields")


def missing_transaction_detail(values: dict) -> dict | None:
    """Validate and normalize optional receipt details for a governed missing sale or return."""
    sign = -1 if values.get("transaction_type") in {"Return", "Refund"} else 1
    try:
        detail = transaction_detail.extract(values, sign)
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError(f"invalid transaction detail: {exc}") from exc
    if detail:
        if detail["discounts"] and not detail["lines"]:
            raise ValueError("discount detail requires at least one item line")
        for tender in detail["tenders"]:
            if tender["tender_type"] not in transaction_detail.TENDER_TYPES:
                raise ValueError("unsupported tender_type")
    return detail


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
    return event_at.replace(tzinfo=UTC) if event_at.tzinfo is None else event_at


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
        ("register_id", 50),
        ("transaction_number", 30),
        ("channel_id", 50),
        ("tender_type", 50),
        ("payment_reference", 255),
        ("settlement_reference", 255),
        ("customer_id", 100),
        ("customer_identification_type", 50),
        ("cashier_id", 100),
        ("salesperson_id", 100),
    ):
        value = values.get(name)
        if value is not None and (not isinstance(value, str) or len(value) > max_length):
            raise ValueError(f"{name} must be a string of at most {max_length} characters")
    number = values.get("transaction_number")
    customer_id = values.get("customer_id")
    customer_identification_type = values.get("customer_identification_type")
    cashier_id = values.get("cashier_id")
    salesperson_id = values.get("salesperson_id")
    if customer_id is not None and (not customer_id.strip() or customer_id != customer_id.strip()):
        raise ValueError("customer_id must not be blank or have surrounding whitespace")
    if customer_identification_type is not None and (
        not customer_identification_type.strip()
        or customer_identification_type != customer_identification_type.strip()
    ):
        raise ValueError(
            "customer_identification_type must not be blank or have surrounding whitespace"
        )
    if salesperson_id is not None and (
        not salesperson_id.strip() or salesperson_id != salesperson_id.strip()
    ):
        raise ValueError("salesperson_id must not be blank or have surrounding whitespace")
    if cashier_id is not None and (not cashier_id.strip() or cashier_id != cashier_id.strip()):
        raise ValueError("cashier_id must not be blank or have surrounding whitespace")
    if number is not None and (not number.isdigit() or not 0 < int(number) <= 2_147_483_647):
        raise ValueError("transaction_number must be a positive 32-bit integer")
    if number is not None and not values.get("register_id"):
        raise ValueError("register_id is required when transaction_number is supplied")


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
