"""FastAPI application composition root."""

import os

from apps.audit.routes import list_recent_events as list_audit_events
from apps.cases.routes import (
    add_case_note,
    apply_evidence_override,
    approve_evidence_override,
    case_collaboration,
    case_evidence,
    case_reconciliation,
    case_stats,
    case_timeline,
    count_cases,
    create_case,
    create_decision,
    create_exception,
    create_finding,
    create_recommendation,
    execute_workflow,
    follow_up_case,
    get_case_detail,
    investigate_case,
    list_cases,
    list_cases_by_status,
    list_decisions,
    list_exceptions,
    list_findings,
    list_policy_evaluations,
    list_recommendations,
    list_validations,
    list_workflows,
    locked_scoped_case,
    override_evidence_request,
    provide_case_evidence,
    reject_evidence_request,
    request_case_evidence,
    review_case_amounts,
    run_validation,
    update_case,
)
from apps.development.routes import seed_demo_data
from apps.request_body import RequestBodyLimitMiddleware
from apps.router_registry import register_routes
from apps.system.lifecycle import lifespan
from apps.system.routes import connector_action, list_connectors
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="Sales Audit Studio API",
    version="1.0.0",
    description="Agentic Financial Exception Management for Retail Commerce",
    lifespan=lifespan,
)

cors_origins_env = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:3000,http://localhost:3001,http://127.0.0.1:3000,http://127.0.0.1:3001",
)
cors_origins = [origin.strip() for origin in cors_origins_env.split(",") if origin.strip()]

register_routes(app)
app.add_middleware(RequestBodyLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Total-Count", "X-Has-More"],
)

# Keep legacy handler imports available while implementations live with their features.
__all__ = [
    "add_case_note",
    "approve_evidence_override",
    "apply_evidence_override",
    "case_collaboration",
    "case_evidence",
    "case_reconciliation",
    "case_stats",
    "case_timeline",
    "connector_action",
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
    "list_audit_events",
    "list_cases",
    "list_cases_by_status",
    "list_connectors",
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
    "run_validation",
    "seed_demo_data",
    "update_case",
]
