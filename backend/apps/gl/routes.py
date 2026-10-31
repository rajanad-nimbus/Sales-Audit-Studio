"""GL cross-reference maintenance and paginated journal previews."""
import math
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.corrections.effects import effective_amounts
from apps.database_errors import violates_unique_constraint
from apps.ingest.manifests import validate_business_date
from apps.security import scope
from auth import require_role
from database import get_db
from models import CanonicalTransaction
from apps.gl.models import GLCrossReference

router = APIRouter(prefix="/api/gl", tags=["General ledger"])
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500


class GLMapBody(BaseModel):
    transaction_type: str = Field(min_length=1, max_length=50)
    tender_type: str | None = Field(default=None, max_length=50)
    debit_account: str = Field(min_length=1, max_length=100)
    credit_account: str = Field(min_length=1, max_length=100)
    active: bool = True

    @field_validator("transaction_type", "debit_account", "credit_account")
    @classmethod
    def require_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Value cannot be blank")
        return value.strip()

    @field_validator("tender_type")
    @classmethod
    def normalize_tender(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


def view(row: GLCrossReference) -> dict:
    return {
        "id": row.id,
        "transaction_type": row.transaction_type,
        "tender_type": row.tender_type,
        "debit_account": row.debit_account,
        "credit_account": row.credit_account,
        "active": row.active,
        "updated_by": row.updated_by,
        "updated_at": row.updated_at,
    }


def _is_duplicate_cross_reference(exc: IntegrityError) -> bool:
    """Recognize either supported GL mapping uniqueness constraint."""
    return violates_unique_constraint(
        exc,
        constraint_names={"uq_gl_cross_reference", "uq_gl_cross_reference_default_by_type"},
        sqlite_messages={
            "UNIQUE constraint failed: gl_cross_references.transaction_type, gl_cross_references.tender_type",
            "UNIQUE constraint failed: gl_cross_references.transaction_type",
        },
    )


@router.get("/cross-references")
async def list_maps(
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("finance", "it")),
):
    rows = (await db.execute(select(GLCrossReference).order_by(
        GLCrossReference.transaction_type, GLCrossReference.tender_type
    ))).scalars().all()
    return [view(row) for row in rows]


@router.post("/cross-references")
async def create_map(
    body: GLMapBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance")),
):
    row = GLCrossReference(
        id=str(uuid.uuid4()),
        **body.model_dump(),
        updated_by=user["user"] or "ontology.user",
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if _is_duplicate_cross_reference(exc):
            raise HTTPException(
                status_code=409,
                detail="Cross-reference already exists for this transaction and tender",
            ) from exc
        raise
    return view(row)


@router.get("/journal-preview")
async def journal_preview(
    store_id: Annotated[str, Query(min_length=1, max_length=50)],
    business_date: str,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it")),
):
    scope.require_store(user, store_id)
    try:
        validate_business_date(business_date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    conditions = (
        CanonicalTransaction.store_id == store_id,
        CanonicalTransaction.business_date == business_date,
    )
    total = (await db.execute(
        select(func.count()).select_from(CanonicalTransaction).where(*conditions)
    )).scalar() or 0
    pages = max(1, math.ceil(total / page_size))
    if page > pages:
        raise HTTPException(status_code=422, detail=f"page must be between 1 and {pages}")

    rows = (await db.execute(
        select(CanonicalTransaction).where(*conditions)
        .order_by(CanonicalTransaction.event_timestamp, CanonicalTransaction.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )).scalars().all()
    amounts = await effective_amounts(db, rows)
    maps = (await db.execute(select(GLCrossReference).where(
        GLCrossReference.active.is_(True)
    ))).scalars().all()
    indexed = {}
    for item in maps:
        key = (item.transaction_type, item.tender_type)
        if key in indexed:
            raise HTTPException(
                status_code=409,
                detail=f"Multiple active GL mappings exist for transaction type {item.transaction_type}",
            )
        indexed[key] = item

    lines, unmapped = [], []
    for transaction in rows:
        mapping = indexed.get((transaction.transaction_type, transaction.tender_type)) or indexed.get(
            (transaction.transaction_type, None)
        )
        if mapping is None:
            unmapped.append(transaction.id)
            continue
        amount = amounts[transaction.id]
        debit, credit = (
            (mapping.debit_account, mapping.credit_account)
            if amount >= 0 else (mapping.credit_account, mapping.debit_account)
        )
        lines.append({
            "transaction_id": transaction.id,
            "debit_account": debit,
            "credit_account": credit,
            "amount": abs(amount),
            "currency": transaction.currency,
        })

    return {
        "store_id": store_id,
        "business_date": business_date,
        "lines": lines,
        "unmapped_transaction_ids": unmapped,
        "total_transactions": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }
