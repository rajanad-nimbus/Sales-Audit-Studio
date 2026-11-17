"""Audit rules over calculated totals, read from Ontology Studio (read-only)."""

from fastapi import APIRouter, Depends

from apps.controls.rules.ontology_view import controls, read_only, total_rule_view
from apps.security.authentication import require_role

router = APIRouter(prefix="/api/audit-rules", tags=["Audit rules"])
READERS = ("finance", "it", "analyst", "auditor", "admin")


@router.get("")
async def list_rules(_: dict = Depends(require_role(*READERS))):
    current = controls()
    return [total_rule_view(rule, current.release_id) for rule in current.total_rules]


@router.get("/{rule_id}/preview")
async def preview_rule(rule_id: str, _: dict = Depends(require_role("finance", "it"))):
    raise read_only()


@router.post("")
async def create_rule(_: dict = Depends(require_role("finance", "it", "admin"))):
    raise read_only()


@router.put("/{rule_id}")
async def update_rule(rule_id: str, _: dict = Depends(require_role("finance", "it", "admin"))):
    raise read_only()


@router.post("/{rule_id}/{action}")
async def change_rule_state(
    rule_id: str, action: str, _: dict = Depends(require_role("finance", "it", "admin"))
):
    """Submit, approve, reject and disable all happen in Ontology Studio."""
    raise read_only()
