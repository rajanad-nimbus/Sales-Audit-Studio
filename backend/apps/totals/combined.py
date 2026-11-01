"""Evaluate combined definitions against their explicitly referenced revisions."""

from decimal import Decimal

from apps.totals.models import AuditTotalDefinition


def calculate_combined(
    definitions: list[AuditTotalDefinition],
    values: dict[str, dict[str | None, Decimal]],
) -> None:
    """Extend values in dependency order; raise ValueError for invalid definitions."""
    by_id = {definition.id: definition for definition in definitions}
    visiting: set[str] = set()

    def evaluate(definition: AuditTotalDefinition) -> dict[str | None, Decimal]:
        if definition.id in values:
            return values[definition.id]
        if definition.id in visiting:
            raise ValueError(f"Circular combined total dependency involving {definition.name}")
        if definition.calculation_basis != "Combined":
            raise ValueError(f"Missing calculated values for {definition.name}")
        ids = definition.combined_total_ids
        if len(ids) != 2 or len(set(ids)) != 2 or definition.combined_operator not in {"+", "-"}:
            raise ValueError(
                f"Combined total {definition.name} requires two distinct totals and an operator"
            )
        references = [by_id.get(item) for item in ids]
        if any(reference is None for reference in references):
            raise ValueError(
                f"Combined total {definition.name} references an inactive or ineffective revision"
            )
        left, right = references
        if any(
            reference.level != definition.level or reference.rollups != definition.rollups
            for reference in references
        ):
            raise ValueError(
                f"Combined total {definition.name} has incompatible levels or roll-ups"
            )
        visiting.add(definition.id)
        first, second = evaluate(left), evaluate(right)
        visiting.remove(definition.id)
        sign = 1 if definition.combined_operator == "+" else -1
        values[definition.id] = {
            key: first.get(key, Decimal(0)) + sign * second.get(key, Decimal(0))
            for key in first.keys() | second.keys()
        }
        return values[definition.id]

    for definition in definitions:
        if definition.calculation_basis == "Combined":
            evaluate(definition)
