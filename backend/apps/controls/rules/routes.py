"""Safe retailer-configured Sales Audit rules."""
import uuid
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from apps.database_errors import violates_unique_constraint
from apps.security.authentication import require_role
from database import get_db
from apps.totals.models import AuditTotalDefinition
from apps.controls.rules.models import ConfiguredAuditRule
from apps.totals.currency import validate_currency
from apps.store_days.freshness import invalidate_configuration, lock_configuration
from apps.transactions.detail import MAX_MONEY, cent_value
router = APIRouter(prefix="/api/audit-rules", tags=["Audit rules"])
class RuleBody(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    total_name: str = Field(min_length=1, max_length=150)
    threshold: Decimal = Field(ge=0, max_digits=15, decimal_places=2)
    threshold_currency: str | None = Field(default="USD", max_length=3)
    severity: str = Field(default="Medium", max_length=20)
    owner: str | None = Field(default=None, max_length=255)
    enabled: bool = True

    @field_validator("name", "total_name")
    @classmethod
    def normalize_required_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Value cannot be blank")
        return normalized

    @field_validator("owner")
    @classmethod
    def normalize_owner(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None
def view(row: ConfiguredAuditRule): return {"id": row.id, "name": row.name, "total_name": row.total_name, "threshold": row.threshold, "threshold_currency": row.threshold_currency, "severity": row.severity, "owner": row.owner, "enabled": row.enabled, "updated_by": row.updated_by, "updated_at": row.updated_at}


def _is_duplicate_rule_name(exc: IntegrityError) -> bool:
    return violates_unique_constraint(
        exc,
        constraint_names={"configured_audit_rules_name_key"},
        sqlite_messages={"UNIQUE constraint failed: configured_audit_rules.name"},
    )


@router.get("")
async def list_rules(db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("finance", "it"))): return [view(row) for row in (await db.execute(select(ConfiguredAuditRule).order_by(ConfiguredAuditRule.name))).scalars().all()]
@router.post("")
async def create_rule(body: RuleBody, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance"))):
    await lock_configuration(db, exclusive=True)
    try:
        threshold = cent_value(body.threshold, "threshold")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if threshold > MAX_MONEY or body.severity not in {"Low", "Medium", "High", "Critical"}:
        raise HTTPException(status_code=422, detail="Threshold must be a non-negative amount with at most two decimal places and severity valid")
    if body.threshold_currency is not None:
        try: validate_currency(body.threshold_currency)
        except ValueError as exc: raise HTTPException(status_code=422, detail=str(exc)) from exc
    else:
        name = body.total_name.removeprefix("Configured: ") if body.total_name.startswith("Configured: ") else None
        definition = (await db.execute(select(AuditTotalDefinition).where(AuditTotalDefinition.name == name)
                      .order_by(AuditTotalDefinition.revision.desc()).limit(1))).scalar_one_or_none() if name else None
        if not definition or definition.aggregation != "Count":
            raise HTTPException(status_code=422, detail="A currency-free threshold requires a configured Count total")
    if body.enabled:
        await invalidate_configuration(db, actor=user["user"] or "ontology.user", reason=f"Audit rule {body.name} added")
    row = ConfiguredAuditRule(id=str(uuid.uuid4()), **body.model_dump(exclude={"threshold"}), threshold=threshold,
                              updated_by=user["user"] or "ontology.user")
    db.add(row)
    try: await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if _is_duplicate_rule_name(exc):
            raise HTTPException(status_code=409, detail="Rule name already exists") from exc
        raise
    return view(row)
