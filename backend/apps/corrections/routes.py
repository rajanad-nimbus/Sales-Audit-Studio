"""Governed transaction correction proposals and approvals."""
import hashlib
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_role
from database import get_db
from models import CanonicalTransaction, FoundationReference, SourceRecord, TransactionAdjustment, utcnow
from nimbus import audit

router = APIRouter(prefix="/api/transaction-adjustments", tags=["Transaction corrections"])
ALLOWED = {"Amount", "Tender", "Tax", "Reference", "Item", "MissingTransaction"}

class AdjustmentBody(BaseModel):
    transaction_id: str | None = None
    adjustment_type: str
    proposed_values: dict
    rationale: str

def view(row: TransactionAdjustment):
    return {"id": row.id, "transaction_id": row.transaction_id, "adjustment_type": row.adjustment_type, "proposed_values": row.proposed_values, "rationale": row.rationale, "status": row.status, "proposed_by": row.proposed_by, "reviewed_by": row.reviewed_by, "created_at": row.created_at}

def transaction_view(row: CanonicalTransaction):
    return {"id": row.id, "business_date": row.business_date, "transaction_type": row.transaction_type,
            "signed_amount": row.signed_amount, "currency": row.currency, "store_id": row.store_id,
            "register_id": row.register_id, "tender_type": row.tender_type, "payment_reference": row.payment_reference,
            "settlement_reference": row.settlement_reference, "source_lineage": row.source_lineage,
            "reconciliation_status": row.reconciliation_status}

@router.get("/transactions/search")
async def search_transactions(q: str, limit: int = 12, db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("finance", "it"))):
    term = q.strip()
    if len(term) < 2: raise HTTPException(status_code=422, detail="Enter at least two characters to search transactions")
    like = f"%{term}%"
    rows = (await db.execute(select(CanonicalTransaction).where(or_(CanonicalTransaction.id.ilike(like), CanonicalTransaction.payment_reference.ilike(like), CanonicalTransaction.settlement_reference.ilike(like), CanonicalTransaction.store_id.ilike(like))).order_by(CanonicalTransaction.business_date.desc()).limit(min(limit, 25)))).scalars().all()
    return [transaction_view(row) for row in rows]

@router.get("/transactions/facets")
async def transaction_facets(db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("finance", "it"))):
    stores = (await db.execute(select(CanonicalTransaction.store_id).distinct().order_by(CanonicalTransaction.store_id))).scalars().all()
    tenders = (await db.execute(select(CanonicalTransaction.tender_type).where(CanonicalTransaction.tender_type.is_not(None)).distinct().order_by(CanonicalTransaction.tender_type))).scalars().all()
    types = (await db.execute(select(CanonicalTransaction.transaction_type).distinct().order_by(CanonicalTransaction.transaction_type))).scalars().all()
    items = (await db.execute(select(FoundationReference.code).where(FoundationReference.category == "Item", FoundationReference.active == True).order_by(FoundationReference.code))).scalars().all()  # noqa: E712
    return {"stores": stores, "tenders": tenders, "transaction_types": types, "items": items}

@router.get("/transactions/{transaction_id}")
async def get_transaction(transaction_id: str, db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("finance", "it"))):
    row = (await db.execute(select(CanonicalTransaction).where(CanonicalTransaction.id == transaction_id))).scalar_one_or_none()
    if not row: raise HTTPException(status_code=404, detail="Canonical transaction not found")
    return transaction_view(row)

@router.get("")
async def list_adjustments(transaction_id: str | None = None, status: str | None = None, db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("finance", "it"))):
    query = select(TransactionAdjustment).order_by(TransactionAdjustment.created_at.desc()).limit(200)
    if transaction_id: query = query.where(TransactionAdjustment.transaction_id == transaction_id)
    if status: query = query.where(TransactionAdjustment.status == status)
    return [view(row) for row in (await db.execute(query)).scalars().all()]

@router.post("")
async def propose_adjustment(body: AdjustmentBody, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance", "it"))):
    if body.adjustment_type not in ALLOWED or not body.rationale.strip() or not body.proposed_values:
        raise HTTPException(status_code=422, detail="A supported adjustment type, proposed values, and rationale are required")
    transaction = None
    if body.adjustment_type == "MissingTransaction":
        required = {"business_date", "store_id", "amount", "transaction_type"}
        missing = required - set(body.proposed_values)
        if body.transaction_id or missing:
            raise HTTPException(status_code=422, detail="MissingTransaction requires no transaction_id and values for business_date, store_id, amount, and transaction_type")
        try:
            if Decimal(str(body.proposed_values["amount"])) <= 0: raise ValueError()
            datetime.fromisoformat(str(body.proposed_values["business_date"]))
        except (InvalidOperation, ValueError):
            raise HTTPException(status_code=422, detail="MissingTransaction amount must be positive and business_date must be ISO-8601")
    else:
        if not body.transaction_id: raise HTTPException(status_code=422, detail="transaction_id is required for this correction type")
        transaction = (await db.execute(select(CanonicalTransaction).where(CanonicalTransaction.id == body.transaction_id))).scalar_one_or_none()
        if not transaction: raise HTTPException(status_code=404, detail="Source transaction not found")
    adjustment = TransactionAdjustment(id=str(uuid.uuid4()), transaction_id=transaction.id if transaction else None, adjustment_type=body.adjustment_type, proposed_values=body.proposed_values, rationale=body.rationale.strip(), proposed_by=user["user"] or "ontology.user")
    db.add(adjustment); audit(db, "TRANSACTION_ADJUSTMENT_PROPOSED", None, "TransactionAdjustment", adjustment.id, f"{body.adjustment_type} adjustment proposed", actor=adjustment.proposed_by)
    await db.commit(); return view(adjustment)

@router.post("/{adjustment_id}/approve")
async def approve_adjustment(adjustment_id: str, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance"))):
    adjustment = (await db.execute(select(TransactionAdjustment).where(TransactionAdjustment.id == adjustment_id))).scalar_one_or_none()
    if not adjustment or adjustment.status != "Proposed": raise HTTPException(status_code=409, detail="Proposed adjustment not found")
    adjustment.status, adjustment.reviewed_by, adjustment.reviewed_at = "Approved", user["user"] or "ontology.user", utcnow()
    if adjustment.adjustment_type == "MissingTransaction":
        values = adjustment.proposed_values
        amount = Decimal(str(values["amount"])).quantize(Decimal("0.01"))
        transaction_type = str(values["transaction_type"])
        signed = -amount if transaction_type in {"Return", "Refund"} else amount
        business_date = str(values["business_date"])
        event_at = datetime.fromisoformat(str(values.get("event_timestamp") or f"{business_date}T00:00:00+00:00").replace("Z", "+00:00"))
        if event_at.tzinfo is None: event_at = event_at.replace(tzinfo=timezone.utc)
        source_id = str(uuid.uuid4())
        payload = json.dumps({"adjustment_id": adjustment.id, "values": values, "rationale": adjustment.rationale}, sort_keys=True, default=str).encode()
        db.add(SourceRecord(id=source_id, source_system="ManualAdjustment", source_version="1.0", source_record_id=f"adjustment-{adjustment.id}", delivery_id=adjustment.id, payload=payload, payload_hash=hashlib.sha256(payload).hexdigest(), received_at=utcnow(), source_event_time=event_at, status="Processed", extracted_at=utcnow()))
        transaction_id = str(uuid.uuid4())
        db.add(CanonicalTransaction(id=transaction_id, business_date=business_date, transaction_date=event_at, event_timestamp=event_at, processing_timestamp=utcnow(), settlement_date=str(values.get("settlement_date") or business_date), transaction_type=transaction_type, transaction_subtype=None, source_lineage="manual-adjustment-v1", currency=str(values.get("currency") or "USD"), signed_amount=signed, quantity=values.get("quantity"), store_id=str(values["store_id"]), register_id=values.get("register_id"), channel_id=str(values.get("channel_id") or "store"), tender_type=values.get("tender_type"), payment_reference=values.get("payment_reference"), settlement_reference=values.get("settlement_reference"), reconciliation_status="Unmatched", source_record_id=source_id, disposition="Open"))
        adjustment.transaction_id = transaction_id
    audit(db, "TRANSACTION_ADJUSTMENT_APPROVED", None, "TransactionAdjustment", adjustment.id, "Transaction adjustment approved", actor=adjustment.reviewed_by, after={"transaction_id": adjustment.transaction_id})
    await db.commit(); return view(adjustment)

@router.post("/{adjustment_id}/reject")
async def reject_adjustment(adjustment_id: str, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance"))):
    adjustment = (await db.execute(select(TransactionAdjustment).where(TransactionAdjustment.id == adjustment_id))).scalar_one_or_none()
    if not adjustment or adjustment.status != "Proposed": raise HTTPException(status_code=409, detail="Proposed adjustment not found")
    adjustment.status, adjustment.reviewed_by, adjustment.reviewed_at = "Rejected", user["user"] or "ontology.user", utcnow()
    audit(db, "TRANSACTION_ADJUSTMENT_REJECTED", None, "TransactionAdjustment", adjustment.id, "Transaction adjustment rejected", actor=adjustment.reviewed_by)
    await db.commit(); return view(adjustment)
