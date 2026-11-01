from decimal import Decimal

import pytest
from apps.controls.rules.routes import RuleBody
from pydantic import ValidationError


@pytest.mark.parametrize("threshold", ["1.001", "10000000000000", "NaN"])
def test_rule_threshold_rejects_values_outside_database_precision(threshold):
    with pytest.raises(ValidationError):
        RuleBody(name="High sales", total_name="POS Net", threshold=threshold)


def test_rule_threshold_accepts_supported_cent_precision():
    rule = RuleBody(name="High sales", total_name="POS Net", threshold="12.30")

    assert rule.threshold == Decimal("12.30")


@pytest.mark.parametrize("values", [
    {"name": " ", "total_name": "POS Net"},
    {"name": "x" * 151, "total_name": "POS Net"},
    {"name": "High sales", "total_name": "x" * 151},
    {"name": "High sales", "total_name": "POS Net", "owner": "x" * 256},
    {"name": "High sales", "total_name": "POS Net", "threshold_currency": "USDX"},
])
def test_rule_request_rejects_values_outside_database_contract(values):
    with pytest.raises(ValidationError):
        RuleBody(threshold="1.00", **values)


def test_rule_request_normalizes_names_and_owner():
    rule = RuleBody(name=" High sales ", total_name=" POS Net ", owner=" analyst ", threshold="1.00")

    assert (rule.name, rule.total_name, rule.owner) == ("High sales", "POS Net", "analyst")
