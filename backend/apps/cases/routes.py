"""Router composition and compatibility exports for the cases application."""

from fastapi import APIRouter

from apps.cases.evidence import (
    add_case_note,
    apply_evidence_override,
    approve_evidence_override,
    case_collaboration,
    case_evidence,
    case_reconciliation,
    case_timeline,
    override_evidence_request,
    provide_case_evidence,
    reject_evidence_request,
    request_case_evidence,
)
from apps.cases.evidence import (
    router as evidence_router,
)
from apps.cases.lineage_routes import (
    router as lineage_router,
)
from apps.cases.records import (
    create_decision,
    create_exception,
    create_finding,
    create_recommendation,
    list_decisions,
    list_exceptions,
    list_findings,
    list_recommendations,
)
from apps.cases.records import (
    router as records_router,
)
from apps.cases.routes_lifecycle import (
    case_stats,
    count_cases,
    create_case,
    follow_up_case,
    get_case_detail,
    list_cases,
    list_cases_by_status,
    review_case_amounts,
    update_case,
)
from apps.cases.routes_lifecycle import (
    router as lifecycle_router,
)
from apps.cases.service import locked_scoped_case
from apps.cases.workflows import (
    execute_workflow,
    investigate_case,
    list_policy_evaluations,
    list_validations,
    list_workflows,
    run_validation,
)
from apps.cases.workflows import (
    router as workflows_router,
)

router = APIRouter()
router.include_router(lifecycle_router)
router.include_router(evidence_router)
router.include_router(lineage_router)
router.include_router(records_router)
router.include_router(workflows_router)

__all__ = [
    "add_case_note",
    "approve_evidence_override",
    "apply_evidence_override",
    "case_collaboration",
    "case_evidence",
    "case_reconciliation",
    "case_stats",
    "case_timeline",
    "count_cases",
    "create_case",
    "create_decision",
    "create_exception",
    "create_finding",
    "create_recommendation",
    "execute_workflow",
    "follow_up_case",
    "get_case_detail",
    "investigate_case",
    "list_cases",
    "list_cases_by_status",
    "list_decisions",
    "list_exceptions",
    "list_findings",
    "list_policy_evaluations",
    "list_recommendations",
    "list_validations",
    "list_workflows",
    "locked_scoped_case",
    "override_evidence_request",
    "provide_case_evidence",
    "reject_evidence_request",
    "request_case_evidence",
    "review_case_amounts",
    "router",
    "run_validation",
    "update_case",
]
