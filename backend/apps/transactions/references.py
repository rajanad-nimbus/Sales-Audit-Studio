"""Normalize the four retailer-defined transaction reference slots."""

from __future__ import annotations

REFERENCE_FIELDS = tuple(f"reference_{number}" for number in range(1, 5))
MAX_REFERENCE_LENGTH = 255


def normalize_user_references(value: object) -> dict[str, str] | None:
    """Accept RTLOG's numbered list or a field map and return bounded non-empty reference slots."""
    if value in (None, [], {}):
        return None
    if isinstance(value, list):
        references: dict[str, str] = {}
        for row in value:
            if not isinstance(row, dict):
                raise ValueError("user_references entries must be objects")
            field = row.get("field")
            if type(field) is not int or not 1 <= field <= len(REFERENCE_FIELDS):
                raise ValueError("user_references field must be a number from 1 to 4")
            key = REFERENCE_FIELDS[field - 1]
            if key in references:
                raise ValueError(f"duplicate user_references field {field}")
            references[key] = validate_reference(row.get("value"), key)
    elif isinstance(value, dict):
        if set(value) - set(REFERENCE_FIELDS):
            raise ValueError("user_references may only contain reference_1 through reference_4")
        references = {key: validate_reference(item, key) for key, item in value.items()}
    else:
        raise ValueError("user_references must be a numbered list or reference field map")
    non_empty = {key: item for key, item in references.items() if item}
    return non_empty or None


def validate_reference(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    if len(value) > MAX_REFERENCE_LENGTH:
        raise ValueError(f"{field} exceeds {MAX_REFERENCE_LENGTH} characters")
    return value.strip()
