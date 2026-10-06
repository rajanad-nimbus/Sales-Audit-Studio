"""Safe retailer-configured Sales Audit rules."""
import uuid
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from auth import require_role
from database import get_db
from models import ConfiguredAuditRule
router = APIRouter(prefix="/api/audit-rules", tags=["Audit rules"])
class RuleBody(BaseModel):
    name: str
    total_name: str
    threshold: Decimal
    severity: str = "Medium"
    owner: str | None = None
    enabled: bool = True
def view(row: ConfiguredAuditRule): return {"id": row.id, "name": row.name, "total_name": row.total_name, "threshold": row.threshold, "severity": row.severity, "owner": row.owner, "enabled": row.enabled, "updated_by": row.updated_by, "updated_at": row.updated_at}
@router.get("")
async def list_rules(db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("finance", "it"))): return [view(row) for row in (await db.execute(select(ConfiguredAuditRule).order_by(ConfiguredAuditRule.name))).scalars().all()]
@router.post("")
async def create_rule(body: RuleBody, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance"))):
    if body.threshold < 0 or body.severity not in {"Low", "Medium", "High", "Critical"}: raise HTTPException(status_code=422, detail="Threshold must be non-negative and severity valid")
    row = ConfiguredAuditRule(id=str(uuid.uuid4()), **body.model_dump(), updated_by=user["user"] or "ontology.user")
    db.add(row)
    try: await db.commit()
    except Exception: await db.rollback(); raise HTTPException(status_code=409, detail="Rule name already exists")
    return view(row)
