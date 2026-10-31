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
        "combined_operator": None,
        "start_date": None,
        "end_date": None,
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


@pytest.mark.parametrize("dates", [
    {"start_date": "2026-02-30"},
    {"end_date": "20261001"},
    {"start_date": "2026-10-02", "end_date": "2026-10-01"},
])
def test_invalid_effective_dates_are_rejected(dates):
    with pytest.raises(ValueError):
        validate_definition(definition(**dates))


def test_combined_total_requires_distinct_inputs():
    with pytest.raises(ValueError, match="two distinct"):
        validate_definition(definition(calculation_basis="Combined", combined_total_ids=["a", "a"]))
