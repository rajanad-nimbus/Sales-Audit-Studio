"""Retailer-configurable audit total definitions."""
import uuid
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from auth import require_role
from database import get_db
from models import AuditTotalDefinition, CanonicalTransaction, SalesAuditErrorDefinition
from .domain import serialize_definition, validate_definition

router = APIRouter(prefix="/api/total-definitions", tags=["Audit totals"])


class TotalDefinitionBody(BaseModel):
    name: str
    aggregation: str = "Sum"
    source_system: str | None = None
    transaction_types: list[str] = []
    tender_type: str | None = None
    level: str = "Store"
    active: bool = True
    revision: int | None = None
    description: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    category: str = "Miscellaneous"
    over_short_group: str | None = None
    over_short_operator: str | None = None
    calculation_basis: str = "Raw"
    status: str = "Worksheet"
    corresponds_to_rtlog: bool = False
    system_calculated: bool = True
    created_with_wizard: bool = True
    required_by_system: bool = False
    display_group: int = 0
    realms: list[dict] = []
    joins: list[dict] = []
    parameters: list[dict] = []
    rollups: list[dict] = []
    restrictions: list[dict] = []
    combined_total_ids: list[str] = []
    location_traits: list[str] = []
    usages: list[dict] = []
    sql_statement: str | None = None
def view(row: AuditTotalDefinition) -> dict[str, object]:
    return serialize_definition(row)
@router.get("")
async def list_definitions(db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("finance", "it"))): return [view(row) for row in (await db.execute(select(AuditTotalDefinition).order_by(AuditTotalDefinition.name))).scalars().all()]
@router.post("")
async def create_definition(body: TotalDefinitionBody, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance"))):
    try: validate_definition(body)
    except ValueError as exc: raise HTTPException(status_code=422, detail=str(exc)) from exc
    latest = (await db.execute(select(func.max(AuditTotalDefinition.revision)).where(AuditTotalDefinition.name == body.name))).scalar() or 0
    row = AuditTotalDefinition(id=str(uuid.uuid4()), revision=int(latest) + 1, **body.model_dump(exclude={"revision"}), updated_by=user["user"] or "ontology.user")
    db.add(row)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="A total definition revision already exists") from exc
    return view(row)

@router.get("/{definition_id}/revisions")
async def revision_history(definition_id: str, db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("finance", "it"))):
    current = (await db.execute(select(AuditTotalDefinition).where(AuditTotalDefinition.id == definition_id))).scalar_one_or_none()
    if not current:
        raise HTTPException(status_code=404, detail="Total definition not found")
    rows = (await db.execute(select(AuditTotalDefinition).where(AuditTotalDefinition.name == current.name).order_by(AuditTotalDefinition.revision))).scalars().all()
    return [view(row) for row in rows]

@router.put("/{definition_id}")
async def update_definition(definition_id: str, body: TotalDefinitionBody, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance"))):
    current = (await db.execute(select(AuditTotalDefinition).where(AuditTotalDefinition.id == definition_id))).scalar_one_or_none()
    if not current:
        raise HTTPException(status_code=404, detail="Total definition not found")
    try: validate_definition(body)
    except ValueError as exc: raise HTTPException(status_code=422, detail=str(exc)) from exc
    latest = (await db.execute(select(func.max(AuditTotalDefinition.revision)).where(AuditTotalDefinition.name == current.name))).scalar() or current.revision
    payload = body.model_dump(exclude={"revision", "name"})
    row = AuditTotalDefinition(id=str(uuid.uuid4()), name=current.name, revision=int(latest) + 1, **payload, updated_by=user["user"] or "ontology.user")
    db.add(row)
    await db.commit()
    return view(row)

@router.get("/errors")
async def list_errors(db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("analyst", "finance", "it", "auditor"))):
    rows = (await db.execute(select(SalesAuditErrorDefinition).where(SalesAuditErrorDefinition.active.is_(True)).order_by(SalesAuditErrorDefinition.code))).scalars().all()
    return [{c.name: getattr(row, c.name) for c in row.__table__.columns} for row in rows]
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
