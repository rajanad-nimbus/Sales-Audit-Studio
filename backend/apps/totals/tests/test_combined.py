from decimal import Decimal

import pytest

from apps.totals.combined import calculate_combined
from models import AuditTotalDefinition


def combined(identifier, left, right, operator="+"):
    return AuditTotalDefinition(id=identifier, name=identifier, level="Register", rollups=[],
                                calculation_basis="Combined", combined_total_ids=[left, right],
                                combined_operator=operator)


def test_combined_totals_follow_dependencies_and_align_dimensions():
    a = AuditTotalDefinition(id="a", name="a", level="Register", rollups=[], calculation_basis="Raw")
    b = AuditTotalDefinition(id="b", name="b", level="Register", rollups=[], calculation_basis="Raw")
    c = combined("c", "a", "b", "-")
    d = combined("d", "c", "b")
    values = {"a": {"R1": Decimal(10)}, "b": {"R1": Decimal(3), "R2": Decimal(4)}}
    calculate_combined([d, b, c, a], values)
    assert values["c"] == {"R1": Decimal(7), "R2": Decimal(-4)}
    assert values["d"] == {"R1": Decimal(10), "R2": Decimal(0)}


def test_combined_rejects_cycles_and_missing_revisions():
    a = combined("a", "b", "c")
    b = combined("b", "a", "c")
    c = AuditTotalDefinition(id="c", name="c", level="Register", rollups=[], calculation_basis="Raw")
    with pytest.raises(ValueError, match="Circular"):
        calculate_combined([a, b, c], {"c": {}})
    with pytest.raises(ValueError, match="inactive or ineffective"):
        calculate_combined([a, c], {"c": {}})
