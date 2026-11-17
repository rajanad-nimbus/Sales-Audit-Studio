"""Read-only views of the audit rules maintained in Ontology Studio.

ZeTSA neither authors nor approves audit rules. These views show the rules of the current approved
ontology release; every change is made, reviewed and approved in Ontology Studio.
"""

from fastapi import HTTPException
from integrations.ontology.audit_controls import TotalRule, TransactionRule
from integrations.ontology.ontology_integration import integration

MAINTAINED_IN_ONTOLOGY = (
    "Audit rules are maintained in Ontology Studio. Change and approve the rule there; "
    "ZeTSA reads the approved release."
)
_COMMON = ("start_date", "end_date", "location_traits", "execution_order", "stop_processing", "owner")


def read_only() -> HTTPException:
    return HTTPException(status_code=409, detail=MAINTAINED_IN_ONTOLOGY)


def _origin(release_id) -> dict:
    return {
        "source": "ontology",
        "ontology_release": release_id,
        "status": "Active",
        "enabled": True,
        "revision": 1,
        "submitted_by": None,
        "submitted_at": None,
        "approved_by": None,
        "approved_at": None,
        "updated_by": None,
        "updated_at": None,
    }


def total_rule_view(rule: TotalRule, release_id) -> dict:
    return {
        "id": rule.id,
        "name": rule.name,
        "total_name": rule.total_name,
        "balance_level": rule.balance_level,
        "comparison": rule.comparison,
        "threshold": rule.threshold,
        "threshold_currency": rule.threshold_currency,
        "severity": rule.severity,
        "location_traits": list(rule.location_traits),
        **{key: getattr(rule, key) for key in _COMMON if key != "location_traits"},
        **_origin(release_id),
    }


def transaction_rule_view(rule: TransactionRule, release_id) -> dict:
    return {
        "id": rule.id,
        "name": rule.name,
        "error_code": rule.error_code,
        "record_type": rule.record_type,
        "conditions": [dict(c) for c in rule.conditions],
        "match_mode": rule.match_mode,
        "evaluation_scope": rule.evaluation_scope,
        "location_traits": list(rule.location_traits),
        **{key: getattr(rule, key) for key in _COMMON if key != "location_traits"},
        **_origin(release_id),
    }


def controls():
    """The current approved controls (raises AuditRulesUnavailable, rendered as a 503)."""
    return integration.audit_controls()
