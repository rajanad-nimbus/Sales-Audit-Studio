"""Retailer-configurable audit total definitions."""
import uuid
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from auth import require_role
from database import get_db
from models import AuditTotalDefinition, CanonicalTransaction

router = APIRouter(prefix="/api/total-definitions", tags=["Audit totals"])
class TotalDefinitionBody(BaseModel):
    name: str
    aggregation: str = "Sum"
    source_system: str | None = None
    transaction_types: list[str] = []
    tender_type: str | None = None
    level: str = "Store"
    active: bool = True
def view(row: AuditTotalDefinition): return {"id": row.id, "name": row.name, "aggregation": row.aggregation, "source_system": row.source_system, "transaction_types": row.transaction_types, "tender_type": row.tender_type, "level": row.level, "active": row.active, "updated_by": row.updated_by, "updated_at": row.updated_at}
@router.get("")
async def list_definitions(db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("finance", "it"))): return [view(row) for row in (await db.execute(select(AuditTotalDefinition).order_by(AuditTotalDefinition.name))).scalars().all()]
@router.post("")
async def create_definition(body: TotalDefinitionBody, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance"))):
    if body.aggregation not in {"Sum", "Count"} or body.level not in {"Store", "Register", "Tender"}: raise HTTPException(status_code=422, detail="Unsupported aggregation or total level")
    row = AuditTotalDefinition(id=str(uuid.uuid4()), **body.model_dump(), updated_by=user["user"] or "ontology.user")
    db.add(row)
    try: await db.commit()
    except Exception: await db.rollback(); raise HTTPException(status_code=409, detail="A total definition with this name already exists")
    return view(row)
@router.get("/{definition_id}/preview")
async def preview_definition(definition_id: str, store_id: str, business_date: str, db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("finance", "it"))):
    definition = (await db.execute(select(AuditTotalDefinition).where(AuditTotalDefinition.id == definition_id))).scalar_one_or_none()
    if not definition: raise HTTPException(status_code=404, detail="Total definition not found")
    rows = (await db.execute(select(CanonicalTransaction).where(CanonicalTransaction.store_id == store_id, CanonicalTransaction.business_date == business_date))).scalars().all()
    if definition.source_system: rows = [row for row in rows if row.source_lineage.startswith(definition.source_system.lower())]
    if definition.transaction_types: rows = [row for row in rows if row.transaction_type in definition.transaction_types]
    if definition.tender_type: rows = [row for row in rows if row.tender_type == definition.tender_type]
    value = sum((row.signed_amount for row in rows), Decimal("0")) if definition.aggregation == "Sum" else len(rows)
    return {"definition": view(definition), "store_id": store_id, "business_date": business_date, "value": value, "record_count": len(rows)}
