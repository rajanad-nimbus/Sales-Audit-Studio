"""Export of the audit rules in the current approved ontology release; import is retired.

Rules are exchanged between environments by releasing them in Ontology Studio, not by file.
"""

from fastapi import APIRouter, Depends

from apps.controls.rules.ontology_view import controls, read_only
from apps.security.authentication import require_role

router = APIRouter(prefix="/api/audit-rules/package", tags=["Audit rules"])
PACKAGED_FIELDS = (
    "name",
    "total_name",
    "start_date",
    "end_date",
    "location_traits",
    "execution_order",
    "stop_processing",
    "balance_level",
    "comparison",
    "threshold",
    "threshold_currency",
    "severity",
    "owner",
)


@router.get("")
async def export_rule_package(
    _: dict = Depends(require_role("finance", "it", "analyst", "auditor", "admin")),
):
    current = controls()
    return {
        "format": "zetsa.audit-rules",
        "version": 1,
        "ontology_release": current.release_id,
        "rules": [
            {
                key: (str(getattr(rule, key)) if key == "threshold" else (
                    list(getattr(rule, key)) if key == "location_traits" else getattr(rule, key)
                ))
                for key in PACKAGED_FIELDS
            }
            for rule in current.total_rules
        ],
    }


@router.post("/preview")
async def preview_rule_package(_: dict = Depends(require_role("finance", "it", "admin"))):
    raise read_only()


@router.post("/apply")
async def apply_rule_package(_: dict = Depends(require_role("finance", "it", "admin"))):
    raise read_only()
