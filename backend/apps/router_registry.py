"""Application route composition for backend feature modules."""

from fastapi import FastAPI

from apps.agents.action_center import router as action_router
from apps.agents.routes import router as agent_router
from apps.assistant.routes import router as assistant_router
from apps.audit.error_routes import router as audit_error_router
from apps.audit.routes import router as audit_router
from apps.cases.routes import router as cases_router
from apps.cash.ach_routes import router as ach_router
from apps.cash.routes import router as cash_router
from apps.command_center.routes import router as command_center_router
from apps.controls.foundation.routes import router as foundation_router
from apps.controls.foundation.tenders import router as tender_router
from apps.controls.rules.routes import router as rules_router
from apps.controls.rules.transaction_routes import router as transaction_rules_router
from apps.corrections.routes import router as corrections_router
from apps.development.routes import router as development_router
from apps.diagnostics.routes import router as diagnostics_router
from apps.exports.routes import router as exports_router
from apps.gl.routes import router as gl_router
from apps.ingest.routes import router as ingest_router
from apps.ingest.shopify.routes import router as shopify_router
from apps.ontology.routes import router as ontology_router
from apps.operations.batch_routes import router as batch_router
from apps.operations.routes import router as operations_router
from apps.reports.routes import router as reports_router
from apps.security.middleware import ontology_authentication
from apps.security.routes import router as security_router
from apps.store_days.closures import closure_router
from apps.store_days.io_log import router as store_day_io_log_router
from apps.store_days.periods import router as period_router
from apps.store_days.routes import router as store_day_router
from apps.store_days.sequence_gap_routes import router as sequence_gap_router
from apps.store_days.total_history import router as total_history_router
from apps.store_days.variance_routes import explanation_router, tolerance_router
from apps.system.routes import router as system_router
from apps.totals.misc_summary import router as misc_totals_router
from apps.totals.routes import router as totals_router
from apps.transactions.item_summary import router as item_summary_router
from apps.transactions.register_event_routes import router as register_event_router
from apps.transactions.register_summary import router as register_summary_router
from apps.transactions.routes import router as transactions_router
from apps.transactions.tender_summary import router as tender_summary_router
from apps.vouchers.routes import router as voucher_router

ROUTERS = (
    ingest_router,
    action_router,
    batch_router,
    audit_router,
    audit_error_router,
    development_router,
    diagnostics_router,
    exports_router,
    store_day_router,
    store_day_io_log_router,
    sequence_gap_router,
    total_history_router,
    closure_router,
    explanation_router,
    period_router,
    tolerance_router,
    foundation_router,
    tender_router,
    cash_router,
    ach_router,
    voucher_router,
    reports_router,
    cases_router,
    command_center_router,
    corrections_router,
    transactions_router,
    item_summary_router,
    tender_summary_router,
    register_event_router,
    register_summary_router,
    gl_router,
    totals_router,
    misc_totals_router,
    rules_router,
    transaction_rules_router,
    operations_router,
    shopify_router,
    agent_router,
    assistant_router,
    ontology_router,
    security_router,
    system_router,
)


def register_routes(app: FastAPI) -> None:
    """Attach every feature router and the shared API security middleware."""
    for router in ROUTERS:
        app.include_router(router)
    app.middleware("http")(ontology_authentication)
