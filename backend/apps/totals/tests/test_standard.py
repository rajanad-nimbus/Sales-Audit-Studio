from types import SimpleNamespace

from apps.totals.domain import validate_definition
from apps.totals.standard import STANDARD_TOTALS, TAX_TOTALS

DEFAULTS = {"measure": "Amount", "level": "Store", "status": "Active", "calculation_basis": "Raw", "over_short_group": None,
            "over_short_operator": None, "combined_total_ids": [], "tender_type": None}


def test_every_standard_total_is_valid():
    for total in [*STANDARD_TOTALS, *TAX_TOTALS]:
        validate_definition(SimpleNamespace(**{**DEFAULTS, **total}))


def test_names_are_unique_and_the_required_store_over_short_total_exists():
    names = [t["name"] for t in STANDARD_TOTALS]
    assert len(names) == len(set(names))
    required = [t for t in STANDARD_TOTALS if t.get("required_by_system")]
    assert [t["name"] for t in required] == ["OVRSHT_S"] and required[0]["category"] == "Over/Short"


def test_tax_measure_must_be_a_sum():
    import pytest
    with pytest.raises(ValueError, match="tax total must be a Sum"):
        validate_definition(SimpleNamespace(**{**DEFAULTS, "measure": "Tax", "aggregation": "Count", "category": "Tax"}))
