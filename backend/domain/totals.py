"""Sales Audit total-definition rules.

This module deliberately has no database or web dependencies.  The route layer
adapts HTTP payloads and persistence to these rules, so the business invariants
can be tested without starting FastAPI or a database.
"""
from typing import Any


VALID_AGGREGATIONS = frozenset({"Sum", "Count"})
VALID_LEVELS = frozenset({"Store", "Register", "Cashier", "Tender"})
VALID_STATUSES = frozenset({"Worksheet", "Active", "Inactive"})
VALID_BASES = frozenset({"Raw", "Combined"})
VALID_OVER_SHORT_GROUPS = frozenset({"Accounted For", "Accountable For"})
VALID_OPERATORS = frozenset({"+", "-"})


def validate_definition(definition: Any) -> None:
    """Raise ValueError when a total definition violates domain invariants."""
    if definition.aggregation not in VALID_AGGREGATIONS:
        raise ValueError("Aggregation must be Sum or Count")
    if definition.level not in VALID_LEVELS:
        raise ValueError("Unsupported total balancing level")
    if definition.status not in VALID_STATUSES:
        raise ValueError("Unsupported total status")
    if definition.calculation_basis not in VALID_BASES:
        raise ValueError("Unsupported calculation basis")
    if definition.category == "Over/Short" and (
        definition.over_short_group not in VALID_OVER_SHORT_GROUPS
        or definition.over_short_operator not in VALID_OPERATORS
    ):
        raise ValueError("Over/Short totals require a valid group and operator")
    if definition.calculation_basis == "Combined" and not definition.combined_total_ids:
        raise ValueError("Combined totals require at least one existing total")


def serialize_definition(row: Any) -> dict[str, Any]:
    """Return the stable external representation of a persisted definition."""
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}
