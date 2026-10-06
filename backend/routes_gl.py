"""GL cross-reference and journal preview for Sales Audit."""
import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from auth import require_role
from database import get_db
from models import CanonicalTransaction, GLCrossReference, utcnow

router = APIRouter(prefix="/api/gl", tags=["General ledger"])
class GLMapBody(BaseModel):
    transaction_type: str
    tender_type: str | None = None
    debit_account: str
    credit_account: str
    active: bool = True
def view(row: GLCrossReference): return {"id": row.id, "transaction_type": row.transaction_type, "tender_type": row.tender_type, "debit_account": row.debit_account, "credit_account": row.credit_account, "active": row.active, "updated_by": row.updated_by, "updated_at": row.updated_at}
@router.get("/cross-references")
async def list_maps(db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("finance", "it"))): return [view(row) for row in (await db.execute(select(GLCrossReference).order_by(GLCrossReference.transaction_type, GLCrossReference.tender_type))).scalars().all()]
@router.post("/cross-references")
async def create_map(body: GLMapBody, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance"))):
    row = GLCrossReference(id=str(uuid.uuid4()), **body.model_dump(), updated_by=user["user"] or "ontology.user")
    db.add(row)
    try: await db.commit()
    except Exception: await db.rollback(); raise HTTPException(status_code=409, detail="Cross-reference already exists for this transaction and tender")
    return view(row)
@router.get("/journal-preview")
async def journal_preview(store_id: str, business_date: str, db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("finance", "it"))):
    rows = (await db.execute(select(CanonicalTransaction).where(CanonicalTransaction.store_id == store_id, CanonicalTransaction.business_date == business_date))).scalars().all()
    maps = (await db.execute(select(GLCrossReference).where(GLCrossReference.active == True))).scalars().all()  # noqa: E712
    indexed = {(item.transaction_type, item.tender_type): item for item in maps}
    lines, unmapped = [], []
    for row in rows:
        mapping = indexed.get((row.transaction_type, row.tender_type)) or indexed.get((row.transaction_type, None))
        if not mapping: unmapped.append(row.id); continue
        amount = abs(row.signed_amount)
        debit, credit = (mapping.debit_account, mapping.credit_account) if row.signed_amount >= 0 else (mapping.credit_account, mapping.debit_account)
        lines.append({"transaction_id": row.id, "debit_account": debit, "credit_account": credit, "amount": amount, "currency": row.currency})
    return {"store_id": store_id, "business_date": business_date, "lines": lines, "unmapped_transaction_ids": unmapped}
