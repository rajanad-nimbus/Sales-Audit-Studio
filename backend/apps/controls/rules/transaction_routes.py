"""Transaction audit rules, read from Ontology Studio (read-only)."""

from fastapi import APIRouter, Depends

from apps.controls.rules.ontology_view import controls, read_only, transaction_rule_view
from apps.security.authentication import require_role

router = APIRouter(prefix="/api/transaction-audit-rules", tags=["Transaction audit rules"])
READERS = ("finance", "it", "analyst", "auditor", "admin")
WRITERS = ("finance", "it", "admin")


@router.get("")
async def list_transaction_rules(_: dict = Depends(require_role(*READERS))):
    current = controls()
    return [transaction_rule_view(rule, current.release_id) for rule in current.transaction_rules]


@router.get("/package")
async def export_transaction_rule_package(_: dict = Depends(require_role(*READERS))):
    current = controls()
    return {
        "format": "zetsa.transaction-audit-rules",
        "version": 1,
        "ontology_release": current.release_id,
        "rules": [transaction_rule_view(rule, current.release_id) for rule in current.transaction_rules],
    }


@router.get("/{rule_id}/preview")
async def preview_transaction_rule(rule_id: str, _: dict = Depends(require_role(*WRITERS))):
    raise read_only()


@router.post("")
async def create_transaction_rule(_: dict = Depends(require_role(*WRITERS))):
    raise read_only()


@router.post("/import/preview")
async def preview_transaction_rule_package(_: dict = Depends(require_role(*WRITERS))):
    raise read_only()


@router.post("/import/apply")
async def import_transaction_rule_package(_: dict = Depends(require_role(*WRITERS))):
    raise read_only()


@router.put("/{rule_id}")
async def update_transaction_rule(rule_id: str, _: dict = Depends(require_role(*WRITERS))):
    raise read_only()


@router.post("/{rule_id}/{action}")
async def change_transaction_rule_state(
    rule_id: str, action: str, _: dict = Depends(require_role(*WRITERS))
):
    raise read_only()
