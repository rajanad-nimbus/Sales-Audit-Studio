"""Sales Audit total-definition rules.

This module deliberately has no database or web dependencies.  The route layer
adapts HTTP payloads and persistence to these rules, so the business invariants
can be tested without starting FastAPI or a database.
"""
from datetime import date
from typing import Protocol


class TotalDefinitionLike(Protocol):
    aggregation: str
    measure: str
    level: str
    status: str
    calculation_basis: str
    category: str
    over_short_group: str | None
    over_short_operator: str | None
    combined_total_ids: list[str]
    combined_operator: str | None
    start_date: str | None
    end_date: str | None


class PersistedDefinitionLike(TotalDefinitionLike, Protocol):
    class _Table:
        columns: list[object]

    __table__: _Table


VALID_AGGREGATIONS = frozenset({"Sum", "Count"})
VALID_MEASURES = frozenset({"Amount", "Tax"})
VALID_LEVELS = frozenset({"Store", "Register", "Cashier", "Tender"})
VALID_STATUSES = frozenset({"Worksheet", "Active", "Inactive"})
VALID_BASES = frozenset({"Raw", "Combined"})
VALID_OVER_SHORT_GROUPS = frozenset({"Accounted For", "Accountable For"})
VALID_OPERATORS = frozenset({"+", "-"})


def validate_definition(definition: TotalDefinitionLike) -> None:
    """Raise ValueError when a total definition violates domain invariants."""
    validate_aggregation(definition)
    validate_measure(definition)
    validate_level_and_status(definition)
    validate_category(definition)
    validate_combination(definition)
    validate_effective_dates(definition)


def validate_aggregation(definition: TotalDefinitionLike) -> None:
    """Validate the supported aggregation function."""
    if definition.aggregation not in VALID_AGGREGATIONS:
        raise ValueError("Aggregation must be Sum or Count")


def validate_measure(definition: TotalDefinitionLike) -> None:
    """Validate measure support and aggregation compatibility."""
    if definition.measure not in VALID_MEASURES:
        raise ValueError("Measure must be Amount or Tax")
    if definition.measure == "Tax" and definition.aggregation != "Sum":
        raise ValueError("A tax total must be a Sum")


def validate_level_and_status(definition: TotalDefinitionLike) -> None:
    """Validate the balancing dimension, lifecycle status, and calculation basis."""
    if definition.level not in VALID_LEVELS:
        raise ValueError("Unsupported total balancing level")
    if definition.status not in VALID_STATUSES:
        raise ValueError("Unsupported total status")
    if definition.calculation_basis not in VALID_BASES:
        raise ValueError("Unsupported calculation basis")


def validate_category(definition: TotalDefinitionLike) -> None:
    """Validate category-specific Over/Short settings."""
    if definition.category == "Over/Short" and (
        definition.over_short_group not in VALID_OVER_SHORT_GROUPS
        or definition.over_short_operator not in VALID_OPERATORS
    ):
        raise ValueError("Over/Short totals require a valid group and operator")


def validate_combination(definition: TotalDefinitionLike) -> None:
    """Validate operand identities and operation for combined totals."""
    if definition.calculation_basis == "Combined" and (
        len(definition.combined_total_ids) != 2 or len(set(definition.combined_total_ids)) != 2
    ):
        raise ValueError("Combined totals require two distinct existing totals")
    if definition.calculation_basis == "Combined" and definition.combined_operator not in VALID_OPERATORS:
        raise ValueError("Combined totals require an addition or subtraction operator")


def validate_effective_dates(definition: TotalDefinitionLike) -> None:
    """Validate ISO calendar dates and their ordering."""
    dates: list[date | None] = []
    for value in (definition.start_date, definition.end_date):
        if value is None:
            dates.append(None)
            continue
        try:
            parsed = date.fromisoformat(value)
            if parsed.isoformat() != value:
                raise ValueError
        except (ValueError, TypeError) as exc:
            raise ValueError("Effective dates must use YYYY-MM-DD") from exc
        dates.append(parsed)
    if dates[0] and dates[1] and dates[0] > dates[1]:
        raise ValueError("Start date must not be after end date")


def serialize_definition(row: PersistedDefinitionLike) -> dict[str, object]:
    """Return the stable external representation of a persisted definition."""
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}
