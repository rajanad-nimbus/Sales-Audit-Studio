"""Validation and normalization for retailer-defined transaction customer attributes."""

import math

from apps.security.sensitive_data import mask_pan_value, redact_sensitive_fields

MAX_CUSTOMER_ATTRIBUTES = 50
MAX_ATTRIBUTE_NAME = 64
MAX_ATTRIBUTE_VALUE = 255


def normalize_customer_attributes(value: object) -> dict[str, str] | None:
    """Normalize a bounded map of scalar customer attributes to displayable text values."""
    if value in (None, {}):
        return None
    if not isinstance(value, dict) or len(value) > MAX_CUSTOMER_ATTRIBUTES:
        raise ValueError(
            f"customer_attributes must be an object with at most {MAX_CUSTOMER_ATTRIBUTES} entries"
        )
    attributes: dict[str, str] = {}
    for name, raw_value in value.items():
        if (
            not isinstance(name, str)
            or not name.strip()
            or name != name.strip()
            or len(name) > MAX_ATTRIBUTE_NAME
        ):
            raise ValueError(
                f"customer attribute names must contain 1 to {MAX_ATTRIBUTE_NAME} characters"
            )
        if isinstance(raw_value, bool):
            attribute_value = str(raw_value).lower()
        elif isinstance(raw_value, float) and math.isfinite(raw_value):
            attribute_value = str(raw_value)
        elif isinstance(raw_value, (str, int)):
            attribute_value = str(raw_value)
        else:
            raise ValueError(f"customer attribute {name} must have a scalar value")
        if len(attribute_value) > MAX_ATTRIBUTE_VALUE:
            raise ValueError(f"customer attribute {name} exceeds {MAX_ATTRIBUTE_VALUE} characters")
        if attribute_value:
            attributes[name] = attribute_value
    return attributes or None


def redact_customer_attribute_value(name: str, value: str | None, mask_character="•"):
    """Apply field-name redaction and PAN detection to a single retailer-defined value."""
    if value in (None, ""):
        return value
    redacted = redact_sensitive_fields({name: value}, mask_character).get(name)
    return mask_pan_value(redacted, mask_character)


def redact_customer_attributes(value: object, mask_character="•") -> dict[str, str] | None:
    """Normalize and redact every customer attribute before persistence or display."""
    attributes = normalize_customer_attributes(value)
    if not attributes:
        return None
    return {
        name: safe_value
        for name, item in attributes.items()
        if (safe_value := redact_customer_attribute_value(name, item, mask_character))
    } or None


def redact_customer_attribute_payload(value: object, mask_character="•"):
    """Mask payment fields and PAN-shaped scalars even when an attribute map is malformed."""
    def mask_values(item):
        if isinstance(item, dict):
            return {key: mask_values(child) for key, child in item.items()}
        if isinstance(item, list):
            return [mask_values(child) for child in item]
        return mask_pan_value(item, mask_character)

    return mask_values(redact_sensitive_fields(value, mask_character))
