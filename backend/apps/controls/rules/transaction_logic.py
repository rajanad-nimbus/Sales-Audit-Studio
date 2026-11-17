"""Shared, allowlisted condition evaluation for transaction audit rules."""

from decimal import Decimal, InvalidOperation

from sqlalchemy import and_, false, func, or_

from apps.transactions.models import CanonicalTransaction


def sql_condition(condition: dict):
    """Compile a validated condition to a parameterized SQL predicate."""
    column = getattr(CanonicalTransaction, condition["field"])
    operator = condition["operator"]
    value = condition.get("value")
    numeric = condition["field"] in {
        "signed_amount",
        "quantity",
        "unit_price",
        "gross_amount",
        "tax_amount",
        "amount",
        "rate",
        "taxable_amount",
    }
    if operator == "is_empty":
        expression = column.is_(None) if numeric else or_(column.is_(None), column == "")
    elif operator == "not_empty":
        expression = column.is_not(None) if numeric else and_(column.is_not(None), column != "")
    elif numeric:
        number = Decimal(value)
        expression = {
            "eq": column == number,
            "ne": or_(column.is_(None), column != number),
            "gt": column > number,
            "gte": column >= number,
            "lt": column < number,
            "lte": column <= number,
        }[operator]
    else:
        expression = {
            "eq": column == value,
            "ne": or_(column.is_(None), column != value),
            "contains": func.lower(column).contains(value.casefold(), autoescape=True),
        }[operator]
    return func.coalesce(expression, false())


def comma_list(value: str) -> set[str]:
    """A comma-separated list of exact values, such as the registers a total includes."""
    return {part.strip() for part in value.split(",") if part.strip()}


def condition_matches(condition: dict, transaction) -> bool:
    """Evaluate one validated field predicate without executing retailer-provided code."""
    field = condition["field"]
    operator = condition["operator"]
    actual = getattr(transaction, field)
    expected = condition.get("value")
    if operator == "is_empty":
        return actual is None or not str(actual).strip()
    if operator == "not_empty":
        return actual is not None and bool(str(actual).strip())
    if actual is None:
        return operator == "ne"
    if field in {
        "signed_amount",
        "quantity",
        "unit_price",
        "gross_amount",
        "tax_amount",
        "amount",
        "rate",
        "taxable_amount",
    }:
        try:
            actual_value, expected_value = Decimal(str(actual)), Decimal(str(expected))
        except (InvalidOperation, ValueError):
            return False
        return {
            "eq": actual_value == expected_value,
            "ne": actual_value != expected_value,
            "gt": actual_value > expected_value,
            "gte": actual_value >= expected_value,
            "lt": actual_value < expected_value,
            "lte": actual_value <= expected_value,
        }.get(operator, False)
    actual_text = str(actual)
    expected_text = str(expected)
    return {
        "eq": actual_text == expected_text,
        "ne": actual_text != expected_text,
        "contains": expected_text.casefold() in actual_text.casefold(),
        "in": actual_text in comma_list(expected_text),
        "not_in": actual_text not in comma_list(expected_text),
    }.get(operator, False)
