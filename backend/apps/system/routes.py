"""System endpoints used by platform health checks and deployment readiness."""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import AUTH_ENABLED
from database import get_db
from integrations.ontology import ontology
from integrations.ontology.ontology_integration import integration as ontology_integration
from models import SourceRecord

router = APIRouter()


@router.get("/")
async def root():
    return {
        "message": "Sales Audit Studio API",
        "version": "1.0.0",
        "description": "Agentic Financial Exception Management for Retail Commerce",
    }


@router.get("/api/health")
async def health_check():
    return {
        "status": "healthy",
        "auth_enabled": AUTH_ENABLED,
        "ontology": ontology.ONTOLOGY_VERSION,
        "ontology_integration": ontology_integration.status(),
    }


@router.get("/api/production-readiness")
async def production_readiness(db: AsyncSession = Depends(get_db)):
    """Show the concrete prerequisites before a sales-audit cutover."""
    rows = (
        await db.execute(
            select(SourceRecord.source_system, func.max(SourceRecord.received_at)).group_by(
                SourceRecord.source_system
            )
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
