"""Redact payment credentials from archived and displayed source payloads."""

import json
import re

CARD_NUMBER_FIELDS = {
    "cardnumber",
    "creditcardnumber",
    "creditcardno",
    "ccnum",
    "ccnumber",
    "pan",
    "primaryaccountnumber",
}
REDACTED_FIELDS = CARD_NUMBER_FIELDS | {
    "cardexpiration",
    "cardexpirationdate",
    "cardexpiry",
    "cardexpdate",
    "cvv",
    "cvv2",
    "cvc",
    "cvc2",
    "securitycode",
    "cardsecuritycode",
    "cardverificationvalue",
    "cardverificationnumber",
    "cardverificationcode",
    "cvn",
    "track1",
    "track2",
    "track1data",
    "track2data",
    "track1equivalentdata",
    "track2equivalentdata",
    "trackdata",
    "fulltrackdata",
    "magstripedata",
    "swipedata",
}
PAN_VALUE_FIELDS = {
    "originalreference",
    "reference",
    "reference1",
    "reference2",
    "reference3",
    "reference4",
    "paymentreference",
    "paymentreferencenumber",
    "tenderreference",
    "settlementreference",
    "sourcerecordid",
    "authorization",
    "authorizationcode",
}


def normalized_field_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def redacted_payment_value(field_name: str, value, mask_character: str = "•"):
    """Keep only a card's last four digits; remove expiration and authentication data."""
    if value is None:
        return None
    if normalized_field_name(field_name) in CARD_NUMBER_FIELDS:
        digits = re.sub(r"\D", "", str(value))
        return f"{mask_character * 4}{digits[-4:]}" if len(digits) >= 4 else "[REDACTED]"
    return "[REDACTED]"


def mask_pan_value(value, mask_character: str = "•"):
    """Mask a payment reference when it is a valid, card-shaped PAN, leaving other IDs intact."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return value
    value_text = str(value)
    if not re.fullmatch(r"[\d -]+", value_text.strip()):
        return value
    digits = re.sub(r"\D", "", value_text)
    if not 13 <= len(digits) <= 19:
        return value
    checksum = 0
    for offset, char in enumerate(reversed(digits)):
        digit = int(char)
        if offset % 2:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return f"{mask_character * 4}{digits[-4:]}" if checksum % 10 == 0 else value


def contains_sensitive_payment_data(value) -> bool:
    """Detect explicit card data or a valid PAN embedded in payment-reference fields."""
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = normalized_field_name(str(key))
            if normalized in REDACTED_FIELDS and item not in (None, ""):
                return True
            if normalized in PAN_VALUE_FIELDS and mask_pan_value(item) != item:
                return True
            if contains_sensitive_payment_data(item):
                return True
    elif isinstance(value, list):
        return any(contains_sensitive_payment_data(item) for item in value)
    elif mask_pan_value(value) != value:
        return True
    return False


def redact_sensitive_fields(value, mask_character: str = "•"):
    """Recursively redact PAN, expiration, verification codes, and track data."""
    if isinstance(value, dict):
        output = {}
        for key, item in value.items():
            normalized = normalized_field_name(str(key))
            if normalized in REDACTED_FIELDS:
                output[key] = redacted_payment_value(key, item, mask_character)
            elif normalized in PAN_VALUE_FIELDS:
                output[key] = mask_pan_value(item, mask_character)
            elif normalized in {"userreferences", "retailerreferences"}:
                output[key] = redact_user_reference_values(item, mask_character)
            else:
                output[key] = redact_sensitive_fields(item, mask_character)
        return output
    if isinstance(value, list):
        return [redact_sensitive_fields(item, mask_character) for item in value]
    return value


def redact_user_reference_values(value, mask_character: str = "•"):
    """Mask PAN-shaped values inside numbered reference lists as well as field maps."""
    if isinstance(value, list):
        output = []
        for row in value:
            if isinstance(row, dict) and "field" in row and "value" in row:
                output.append(
                    {
                        key: mask_pan_value(item, mask_character)
                        if key == "value"
                        else redact_sensitive_fields(item, mask_character)
                        for key, item in row.items()
                    }
                )
            else:
                output.append(redact_sensitive_fields(row, mask_character))
        return output
    return redact_sensitive_fields(value, mask_character)


def redact_sensitive_preview(text: str) -> str:
    """Redact payment fields even when a JSON preview is truncated mid-payload."""
    pattern = re.compile(
        r'("(?P<field>[A-Za-z0-9_-]+)"\s*:\s*)'
        r'("(?:\\.|[^"\\])*(?:"|$)|[^,}\]\s]+)',
    )

    def replace(match: re.Match) -> str:
        prefix, raw_value = match.group(1), match.group(2)
        field_name = match.group("field")
        if raw_value.startswith('"'):
            try:
                value = json.loads(raw_value)
            except ValueError:
                value = raw_value[1:].removesuffix('"')
        else:
            value = raw_value
        normalized_name = normalized_field_name(field_name)
        if normalized_name in REDACTED_FIELDS:
            return f"{prefix}{json_string(redacted_payment_value(field_name, value))}"
        if normalized_name in PAN_VALUE_FIELDS:
            masked = mask_pan_value(value)
            if masked != value:
                return f"{prefix}{json_string(masked)}"
        return match.group(0)

    return pattern.sub(replace, text)


def json_string(value) -> str:
    """JSON-encode a string for insertion into a JSON preview."""
    return json.dumps(value, ensure_ascii=False)
