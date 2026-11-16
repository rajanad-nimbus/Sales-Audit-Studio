"""Evaluate combined definitions against their explicitly referenced revisions."""

from collections.abc import Hashable
from decimal import Decimal

from apps.totals.models import AuditTotalDefinition


def calculate_combined(
    definitions: list[AuditTotalDefinition],
    values: dict[str, dict[Hashable, Decimal]],
) -> None:
    """Extend values in dependency order; raise ValueError for invalid definitions."""
    by_id = {definition.id: definition for definition in definitions}
    visiting: set[str] = set()

    def evaluate(
        definition: AuditTotalDefinition,
    ) -> dict[Hashable, Decimal]:
        if definition.id in values:
            return values[definition.id]
        if definition.id in visiting:
            raise ValueError(f"Circular combined total dependency involving {definition.name}")
        if definition.calculation_basis != "Combined":
            raise ValueError(f"Missing calculated values for {definition.name}")
        ids = definition.combined_total_ids
        operators = getattr(definition, "combined_total_operators", None) or [
            definition.combined_operator
        ] * (len(ids) - 1)
        if (
            len(ids) < 2
            or len(ids) > 20
            or len(set(ids)) != len(ids)
            or len(operators) != len(ids) - 1
            or any(operator not in {"+", "-"} for operator in operators)
        ):
            raise ValueError(
                f"Combined total {definition.name} requires 2 to 20 distinct totals and an operator between each pair"
            )
        references = [by_id.get(item) for item in ids]
        if any(reference is None for reference in references):
            raise ValueError(
                f"Combined total {definition.name} references an inactive or ineffective revision"
            )
        if any(
            reference.level != definition.level or reference.rollups != definition.rollups
            for reference in references
        ):
            raise ValueError(
                f"Combined total {definition.name} has incompatible levels or roll-ups"
            )
        visiting.add(definition.id)
        operands = [evaluate(reference) for reference in references]
        visiting.remove(definition.id)
        keys = set().union(*(operand.keys() for operand in operands))
        combined: dict[Hashable, Decimal] = {}
        for key in keys:
            amount = operands[0].get(key, Decimal(0))
            for operator, operand in zip(operators, operands[1:], strict=True):
                sign = 1 if operator == "+" else -1
                amount += sign * operand.get(key, Decimal(0))
            combined[key] = amount
        values[definition.id] = combined
        return values[definition.id]

    for definition in definitions:
        if definition.calculation_basis == "Combined":
            evaluate(definition)
