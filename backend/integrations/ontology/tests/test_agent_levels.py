import pytest

from integrations.ontology import nimbus
from integrations.ontology.audit_controls import AuditRulesUnavailable, parse_controls


def _rule(code, config):
    return {"ruleType": "audit_control", "ruleCode": code, "name": code, "config": config}


LIMITS = [
    _rule("L1", {"family": "policy_limit", "limit": "autonomy_limit", "amount": "1000"}),
    _rule("L2", {"family": "policy_limit", "limit": "timing_autonomy_limit", "amount": "250"}),
]


def _level(code, exception_type, level):
    return _rule(code, {"family": "agent_level", "exception_type": exception_type, "level": level})


def test_a_release_without_levels_uses_the_documented_defaults():
    controls = parse_controls("r", LIMITS)
    assert controls.level_for("DUPLICATE_SALES") == "act"
    assert controls.level_for("AMOUNT_MISMATCH") == "propose"
    assert controls.level_for("MISSING_REFUND") == "collect"
    assert controls.level_for("A_BRAND_NEW_CODE") == "collect"


def test_the_release_can_change_a_level_without_code():
    controls = parse_controls("r", [*LIMITS, _level("A1", "DUPLICATE_SALES", "propose")])
    assert controls.level_for("DUPLICATE_SALES") == "propose"
    assert controls.level_for("SHOPIFY_DUPLICATE_ORDER") == "act"


def test_a_bad_level_rejects_the_release():
    with pytest.raises(AuditRulesUnavailable):
        parse_controls("r", [*LIMITS, _level("A1", "DUPLICATE_SALES", "autopilot")])
    with pytest.raises(AuditRulesUnavailable):
        parse_controls(
            "r",
            [*LIMITS, _level("A1", "DUPLICATE_SALES", "act"), _level("A2", "DUPLICATE_SALES", "collect")],
        )


def test_without_an_approved_release_the_defaults_apply(monkeypatch):
    def unavailable():
        raise AuditRulesUnavailable("none")

    monkeypatch.setattr(nimbus, "policy_limits", unavailable)
    assert nimbus.agent_level("DUPLICATE_SALES") == "act"
    assert nimbus.agent_level("UNKNOWN") == "collect"
