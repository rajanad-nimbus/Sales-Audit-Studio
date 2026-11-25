"""Policy-gate outcomes for the amounts in the NIMBUS_SCENARIOS.md narratives.

These check only the deterministic gate (`evaluate_policy`) with the narrative's own figures; the
narratives' wider workflows are not replayed here.
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest
from integrations.ontology import nimbus


def run(amount, *, completeness=100, disposition="Timing", impact="0", currency="USD"):
    case = SimpleNamespace(
        id="c",
        evidence_completeness=completeness,
        total_exception_amount=Decimal(amount),
        currency=currency,
    )
    rec = SimpleNamespace(id="r", disposition_type=disposition, financial_impact=Decimal(impact))
    return nimbus.evaluate_policy(SimpleNamespace(add=lambda item: None), case, rec)


def test_scenario_1_timing_550_is_above_the_250_limit_so_a_person_decides():
    assert run("550").outcome == "RequireHuman"


@pytest.mark.parametrize("amount", ["250", "100", "0.01"])
def test_scenario_1_timing_at_or_below_the_limit_is_permitted(amount):
    assert run(amount).outcome == "Permit"


def test_timing_with_financial_movement_is_not_permitted_even_below_the_limit():
    assert run("100", impact="100").outcome == "RequireHuman"


def test_scenario_4_return_at_60_percent_evidence_requests_evidence_not_a_decision():
    result = run("94.22", completeness=60, disposition="Correction", impact="94.22")
    assert result.outcome == "RequestEvidence"
    assert "Required evidence incomplete" in result.reasons["reasons"]


def test_scenario_4_return_with_complete_evidence_still_needs_a_person():
    assert run("94.22", disposition="Correction", impact="94.22").outcome == "RequireHuman"


def test_amount_above_the_autonomy_limit_needs_a_person():
    assert run("1500", disposition="Adjustment", impact="1500").outcome == "RequireHuman"
