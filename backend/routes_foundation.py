"""Retail foundation data used by Sales Audit controls."""
import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_role
from database import get_db
from models import FoundationReference, utcnow

router = APIRouter(prefix="/api/foundation", tags=["Retail foundation data"])
VALID_CATEGORIES = {"Store", "Register", "Cashier", "Tender", "Item", "BankAccount", "GLAccount", "TaxCode", "BusinessCalendar"}

class ReferenceBody(BaseModel):
    category: str
    code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=255)
    active: bool = True
    attributes: dict = {}

def view(row: FoundationReference):
    return {"id": row.id, "category": row.category, "code": row.code, "name": row.name,
            "active": row.active, "attributes": row.attributes, "updated_by": row.updated_by, "updated_at": row.updated_at}

@router.get("")
async def list_references(category: str | None = None, db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("finance", "it"))):
    query = select(FoundationReference).order_by(FoundationReference.category, FoundationReference.code)
    if category: query = query.where(FoundationReference.category == category)
    return [view(row) for row in (await db.execute(query)).scalars().all()]

@router.post("")
async def create_reference(body: ReferenceBody, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance", "it"))):
    if body.category not in VALID_CATEGORIES: raise HTTPException(status_code=422, detail=f"category must be one of {sorted(VALID_CATEGORIES)}")
    row = FoundationReference(id=str(uuid.uuid4()), category=body.category, code=body.code.strip(), name=body.name.strip(), active=body.active, attributes=body.attributes, updated_by=user["user"] or "ontology.user")
    db.add(row)
    try: await db.commit()
    except Exception:
        await db.rollback(); raise HTTPException(status_code=409, detail="Reference code already exists in this category")
    await db.refresh(row); return view(row)

@router.put("/{reference_id}")
async def update_reference(reference_id: str, body: ReferenceBody, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance", "it"))):
    row = (await db.execute(select(FoundationReference).where(FoundationReference.id == reference_id))).scalar_one_or_none()
    if not row: raise HTTPException(status_code=404, detail="Foundation reference not found")
    if body.category not in VALID_CATEGORIES: raise HTTPException(status_code=422, detail="Unsupported foundation category")
    row.category, row.code, row.name, row.active, row.attributes, row.updated_by, row.updated_at = body.category, body.code.strip(), body.name.strip(), body.active, body.attributes, user["user"] or "ontology.user", utcnow()
    await db.commit(); await db.refresh(row); return view(row)
