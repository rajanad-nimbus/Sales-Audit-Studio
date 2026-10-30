from types import SimpleNamespace

import pytest

from apps.totals.domain import validate_definition


def definition(**overrides):
    values = {
        "aggregation": "Sum",
        "measure": "Amount",
        "level": "Store",
        "status": "Worksheet",
        "calculation_basis": "Raw",
        "category": "Miscellaneous",
        "over_short_group": None,
        "over_short_operator": None,
        "combined_total_ids": [],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_combined_total_requires_existing_total_references():
    with pytest.raises(ValueError, match="existing total"):
        validate_definition(definition(calculation_basis="Combined"))


def test_over_short_requires_group_and_operator():
    with pytest.raises(ValueError, match="group and operator"):
        validate_definition(definition(category="Over/Short"))


def test_valid_definition_is_accepted():
    validate_definition(definition(category="Over/Short", over_short_group="Accounted For", over_short_operator="+"))
