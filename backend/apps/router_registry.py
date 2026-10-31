"""Application route composition for backend feature modules."""

from fastapi import FastAPI

from apps.agents.action_center import router as action_router
from apps.agents.routes import router as agent_router
from apps.assistant.routes import router as assistant_router
from apps.audit.routes import router as audit_router
from apps.cash.routes import router as cash_router
from apps.cases.routes import router as cases_router
from apps.controls.foundation.routes import router as foundation_router
from apps.controls.rules.routes import router as rules_router
from apps.corrections.routes import router as corrections_router
from apps.development.routes import router as development_router
from apps.exports.routes import router as exports_router
from apps.gl.routes import router as gl_router
from apps.ingest.routes import router as ingest_router
from apps.ingest.shopify.routes import router as shopify_router
from apps.operations.batch import router as batch_router
from apps.operations.routes import router as operations_router
from apps.ontology.routes import router as ontology_router
from apps.security.middleware import ontology_authentication
from apps.security.routes import router as security_router
from apps.store_days.routes import router as store_day_router
from apps.system.routes import router as system_router
from apps.totals.routes import router as totals_router
from apps.transactions.routes import router as transactions_router

ROUTERS = (
    ingest_router, action_router, batch_router, audit_router, development_router, exports_router,
    store_day_router, foundation_router, cash_router, cases_router, corrections_router,
    transactions_router, gl_router, totals_router, rules_router, operations_router,
    shopify_router, agent_router, assistant_router, ontology_router, security_router, system_router,
)


def register_routes(app: FastAPI) -> None:
    """Attach every feature router and the shared API security middleware."""
    for router in ROUTERS:
        app.include_router(router)
    app.middleware("http")(ontology_authentication)
