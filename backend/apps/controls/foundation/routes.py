"""Retail foundation data used by Sales Audit controls."""
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_role
from apps.database_errors import violates_unique_constraint
from apps.store_days.freshness import invalidate_configuration, lock_configuration
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


def _is_duplicate_reference_code(exc: IntegrityError) -> bool:
    return violates_unique_constraint(
        exc,
        constraint_names={"uq_foundation_reference_category_code"},
        sqlite_messages={"UNIQUE constraint failed: foundation_references.category, foundation_references.code"},
    )

@router.get("")
async def list_references(
    category: str | None = Query(default=None, max_length=50),
    categories: str | None = Query(default=None, max_length=500),
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=200, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("finance", "it")),
):
    """Return a bounded page and category counts for the Foundation Data screen."""
    selected = {value.strip() for value in (categories or "").split(",") if value.strip()}
    if category:
        selected.add(category)
    if selected - VALID_CATEGORIES:
        raise HTTPException(status_code=422, detail="Unsupported foundation category")

    filters = []
    if selected:
        filters.append(FoundationReference.category.in_(selected))
    normalized_query = (q or "").strip()
    if normalized_query:
        filters.append(or_(
            func.lower(FoundationReference.code).contains(normalized_query.lower(), autoescape=True),
            func.lower(FoundationReference.name).contains(normalized_query.lower(), autoescape=True),
        ))

    total = (await db.execute(select(func.count()).select_from(FoundationReference).where(*filters))).scalar_one()
    category_counts = dict((await db.execute(
        select(FoundationReference.category, func.count()).group_by(FoundationReference.category)
    )).all())
    rows = (await db.execute(
        select(FoundationReference).where(*filters)
        .order_by(FoundationReference.category, FoundationReference.code, FoundationReference.id)
        .offset(offset).limit(limit)
    )).scalars().all()
    return {"items": [view(row) for row in rows], "total": total, "category_counts": category_counts}

@router.post("")
async def create_reference(body: ReferenceBody, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance", "it"))):
    if body.category not in VALID_CATEGORIES: raise HTTPException(status_code=422, detail=f"category must be one of {sorted(VALID_CATEGORIES)}")
    if body.category == "Store" and body.active and body.attributes.get("location_traits"):
        await invalidate_configuration(db, actor=user["user"] or "ontology.user", reason="Store location traits changed", stores={body.code.strip()})
    row = FoundationReference(id=str(uuid.uuid4()), category=body.category, code=body.code.strip(), name=body.name.strip(), active=body.active, attributes=body.attributes, updated_by=user["user"] or "ontology.user")
    db.add(row)
    try: await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if _is_duplicate_reference_code(exc):
            raise HTTPException(status_code=409, detail="Reference code already exists in this category") from exc
        raise
    await db.refresh(row); return view(row)

@router.put("/{reference_id}")
async def update_reference(reference_id: str, body: ReferenceBody, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance", "it"))):
    await lock_configuration(db, exclusive=True)
    row = (await db.execute(select(FoundationReference).where(FoundationReference.id == reference_id))).scalar_one_or_none()
    if not row: raise HTTPException(status_code=404, detail="Foundation reference not found")
    if body.category not in VALID_CATEGORIES: raise HTTPException(status_code=422, detail="Unsupported foundation category")
    old_traits = row.attributes.get("location_traits") if isinstance(row.attributes, dict) else None
    new_traits = body.attributes.get("location_traits")
    if (row.category == "Store" or body.category == "Store") and (
        row.category != body.category or row.code != body.code.strip() or row.active != body.active or old_traits != new_traits
    ):
        stores = ({row.code} if row.category == "Store" else set()) | ({body.code.strip()} if body.category == "Store" else set())
        await invalidate_configuration(db, actor=user["user"] or "ontology.user", reason="Store location traits changed", stores=stores)
    row.category, row.code, row.name, row.active, row.attributes, row.updated_by, row.updated_at = body.category, body.code.strip(), body.name.strip(), body.active, body.attributes, user["user"] or "ontology.user", utcnow()
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if _is_duplicate_reference_code(exc):
            raise HTTPException(status_code=409, detail="Reference code already exists in this category") from exc
        raise
    await db.refresh(row)
    return view(row)
