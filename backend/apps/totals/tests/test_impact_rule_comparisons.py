"""Impact preview must use the same threshold semantics as store-day auditing."""

from decimal import Decimal

import pytest
from integrations.ontology.audit_controls import TotalRule

from apps.totals.impact import rule_changes


@pytest.mark.parametrize(
    ("comparison", "before", "after", "effect"),
    [
        ("ABS_GT", "11", "2", "Clears"),
        ("GT", "-11", "11", "Newly breaches"),
        ("GTE", "9", "10", "Newly breaches"),
        ("LT", "15", "2", "Newly breaches"),
        ("LTE", "11", "10", "Newly breaches"),
        ("EQ", "9", "10", "Newly breaches"),
        ("NE", "10", "9", "Newly breaches"),
    ],
)
def test_preview_reports_rule_transition_using_its_comparison(comparison, before, after, effect):
    rule = TotalRule(
        id="r",
        name="Threshold",
        total_name="Configured: Sales",
        balance_level="Store",
        comparison=comparison,
        threshold=Decimal("10"),
        threshold_currency="USD",
        severity="Medium",
    )
    changes = rule_changes([rule], {"Sales": Decimal(before)}, {"Sales": Decimal(after)}, "USD")
    assert [change["effect"] for change in changes] == [effect]
