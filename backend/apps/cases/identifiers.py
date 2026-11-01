"""Generate stable-format human-readable case identifiers."""

import uuid

CASE_NUMBER_SUFFIX_LENGTH = 12


def generate_case_number(number_date: str) -> str:
    """Return a date-prefixed case number with a collision-resistant UUID suffix."""
    date_component = number_date.replace("-", "")
    suffix = uuid.uuid4().hex[:CASE_NUMBER_SUFFIX_LENGTH].upper()
    return f"ZA-{date_component}-{suffix}"
