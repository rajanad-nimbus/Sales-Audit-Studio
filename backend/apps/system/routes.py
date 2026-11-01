"""System endpoints used by platform health checks and deployment readiness."""

from database import get_db
from fastapi import APIRouter, Depends, HTTPException
from integrations.ontology.ontology_integration import integration as ontology_integration
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.ingest.models import SourceRecord
from apps.security.authentication import AUTH_ENABLED, require_role
from apps.system.models import Connector

router = APIRouter()


@router.get("/api/connectors")
async def list_connectors(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Connector).order_by(Connector.name))).scalars().all()
    return [
        {
            "id": row.id,
            "name": row.name,
            "state": row.state,
            "mode": row.mode,
            "last_error": row.last_error,
            "last_checked_at": row.last_checked_at,
        }
        for row in rows
    ]


@router.post("/api/connectors/{connector_id}/{action}")
async def connector_action(
    connector_id: str,
    action: str,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_role("it")),
):
    """Reject connector controls until a real adapter can execute them."""
    if action not in {"retry", "pause", "resume"}:
        raise HTTPException(status_code=400, detail="Unknown action")
    connector = await db.get(Connector, connector_id)
    if connector is None:
        raise HTTPException(status_code=404, detail="Connector not found")
    raise HTTPException(
        status_code=501,
        detail=f"{action.capitalize()} is unavailable: {connector.name} has no configured connector adapter",
    )


@router.get("/")
async def root():
    return {
        "message": "Sales Audit Studio API",
        "version": "1.0.0",
        "description": "Agentic Financial Exception Management for Retail Commerce",
    }


@router.get("/api/health")
async def health_check(db: AsyncSession = Depends(get_db)):
    await db.execute(select(1))
    return {
        "status": "healthy",
        "database": "healthy",
    }


@router.get("/api/production-readiness")
async def production_readiness(db: AsyncSession = Depends(get_db)):
    """Show the concrete prerequisites before a sales-audit cutover."""
    rows = (
        await db.execute(
            select(SourceRecord.source_system, func.max(SourceRecord.received_at))
            .where(SourceRecord.status.in_(("Archived", "Processed")))
            .group_by(SourceRecord.source_system)
        )
    ).all()
    received = {source: timestamp for source, timestamp in rows}
    sources = [
        {"source_system": source, "received_at": received.get(source), "ready": source in received}
        for source in ("POS", "POSControl", "Processor", "Bank", "ERP", "Shopify")
    ]
    integration = ontology_integration.status()
    checks = [
        {
            "name": "Ontology user authentication",
            "ready": AUTH_ENABLED,
            "detail": "Nimbus delegates user authentication to Ontology Studio.",
        },
        {
            "name": "Approved Sales Audit ontology",
            "ready": integration["ready"],
            "detail": "Approve and release the rules, then configure an ons_ integration key.",
        },
        {
            "name": "Required source feeds",
            "ready": all(item["ready"] for item in sources),
            "detail": "Configure scheduled read-only POS, control, processor, bank, and ERP deliveries.",
        },
    ]
    return {
        "ready": all(check["ready"] for check in checks),
        "checks": checks,
        "sources": sources,
        "next_inputs": [
            "source schemas and read-only endpoints",
            "matching keys and control totals",
            "cutoff calendar and tolerance policy",
            "approval matrix and escalation owners",
        ],
    }
