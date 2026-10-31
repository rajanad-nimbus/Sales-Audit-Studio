"""Read and refresh the configured retail ontology context."""

from fastapi import APIRouter, Depends, HTTPException

from apps.security.authentication import require_role
from integrations.ontology import ontology
from integrations.ontology.ontology_integration import integration as ontology_integration

router = APIRouter()


@router.get("/api/ontology")
async def ontology_index():
    return {
        "version": ontology.ONTOLOGY_VERSION,
        "exception_types": {
            key: {"label": value["label"], "family": value["family"]}
            for key, value in ontology.EXCEPTION_TYPES.items()
        },
    }


@router.get("/api/ontology/integration/status")
async def ontology_integration_status(_: dict = Depends(require_role("it", "finance", "admin"))):
    """Show integration readiness without exposing credentials."""
    return ontology_integration.status()


@router.post("/api/ontology/integration/refresh")
async def refresh_ontology_integration(_: dict = Depends(require_role("it", "admin"))):
    """Refresh a complete release-pinned ontology context."""
    await ontology_integration.refresh()
    return ontology_integration.status()


@router.get("/api/ontology/integration/context")
async def ontology_integration_context(_: dict = Depends(require_role("it", "finance", "admin"))):
    """Return approved ontology definitions without warehouse data."""
    return ontology_integration.context()


@router.get("/api/ontology/exception-types/{exception_type}")
async def ontology_exception_type(exception_type: str):
    definition = ontology.get(exception_type)
    if not definition:
        raise HTTPException(status_code=404, detail="Unknown exception type")
    return {"version": ontology.ONTOLOGY_VERSION, "type": exception_type, **definition}
