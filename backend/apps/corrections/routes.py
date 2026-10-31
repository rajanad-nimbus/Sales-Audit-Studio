"""Governed transaction correction proposals and approvals."""
import uuid
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import String, case, cast, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.security.authentication import require_role
from apps.search import contains_literal
from apps.security import scope
from apps.corrections.approval import apply_approval_effects, prepare_approval
from apps.corrections.validation import (
    missing_transaction_source,
    validate_adjustment_values,
    validate_missing_transaction,
)
from database import get_db
from apps.transactions.models import CanonicalTransaction
from model_base import utcnow
from apps.corrections.models import TransactionAdjustment
from integrations.ontology.nimbus import audit

router = APIRouter(prefix="/api/transaction-adjustments", tags=["Transaction corrections"])
ALLOWED = {"Amount", "Tender", "Tax", "Reference", "Item", "MissingTransaction"}

class AdjustmentBody(BaseModel):
    transaction_id: str | None = Field(default=None, min_length=1, max_length=36)
    adjustment_type: str = Field(min_length=1, max_length=50)
    proposed_values: dict = Field(min_length=1, max_length=20)
    rationale: str = Field(min_length=1, max_length=5000)


def view(row: TransactionAdjustment):
    return {"id": row.id, "transaction_id": row.transaction_id, "adjustment_type": row.adjustment_type, "proposed_values": row.proposed_values, "rationale": row.rationale, "status": row.status, "proposed_by": row.proposed_by, "reviewed_by": row.reviewed_by, "created_at": row.created_at}

def transaction_view(row: CanonicalTransaction):
    return {"id": row.id, "business_date": row.business_date, "transaction_type": row.transaction_type,
            "signed_amount": row.signed_amount, "currency": row.currency, "store_id": row.store_id,
            "register_id": row.register_id, "tender_type": row.tender_type, "payment_reference": row.payment_reference,
            "settlement_reference": row.settlement_reference, "source_lineage": row.source_lineage,
            "reconciliation_status": row.reconciliation_status}

@router.get("/transactions/search")
async def search_transactions(
    q: Annotated[str, Query(min_length=2, max_length=255)],
    limit: Annotated[int, Query(ge=1, le=25)] = 12,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it")),
):
    term = q.strip()
    if len(term) < 2: raise HTTPException(status_code=422, detail="Enter at least two characters to search transactions")
    query = scope.restrict(select(CanonicalTransaction), user, CanonicalTransaction.store_id)
    rows = (await db.execute(query.where(or_(contains_literal(CanonicalTransaction.id, term),
                                              contains_literal(CanonicalTransaction.payment_reference, term),
                                              contains_literal(CanonicalTransaction.settlement_reference, term),
                                              contains_literal(CanonicalTransaction.store_id, term)))
                        .order_by(CanonicalTransaction.business_date.desc()).limit(min(limit, 25)))).scalars().all()
    return [transaction_view(row) for row in rows]

@router.get("/transactions/facets")
async def transaction_facets(db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance", "it"))):
    stores = (await db.execute(scope.restrict(select(CanonicalTransaction.store_id), user, CanonicalTransaction.store_id).distinct().order_by(CanonicalTransaction.store_id))).scalars().all()
    tenders = (await db.execute(scope.restrict(select(CanonicalTransaction.tender_type), user, CanonicalTransaction.store_id).where(CanonicalTransaction.tender_type.is_not(None)).distinct().order_by(CanonicalTransaction.tender_type))).scalars().all()
    types = (await db.execute(scope.restrict(select(CanonicalTransaction.transaction_type), user, CanonicalTransaction.store_id).distinct().order_by(CanonicalTransaction.transaction_type))).scalars().all()
    return {"stores": stores, "tenders": tenders, "transaction_types": types}

@router.get("/transactions/{transaction_id}")
async def get_transaction(transaction_id: str, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance", "it"))):
    row = (await db.execute(select(CanonicalTransaction).where(CanonicalTransaction.id == transaction_id))).scalar_one_or_none()
    if not row: raise HTTPException(status_code=404, detail="Canonical transaction not found")
    scope.require_store(user, row.store_id)
    return transaction_view(row)

@router.get("")
async def list_adjustments(transaction_id: str | None = None, status: str | None = None,
                           db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance", "it")),
                           page: Annotated[int, Query(ge=1, le=10000)] = 1,
                           page_size: Annotated[int, Query(ge=1, le=200)] = 50,
                           q: Annotated[str | None, Query(max_length=255)] = None):
    """Return a bounded corrections page, accurate counts and optional text filtering."""
    if status is not None and status not in {"Proposed", "Approved", "Rejected"}:
        raise HTTPException(status_code=422, detail="status must be Proposed, Approved, or Rejected")
    query = select(TransactionAdjustment)
    stores = scope.allowed_stores(user)
    if stores is not None:
        query = query.outerjoin(CanonicalTransaction, TransactionAdjustment.transaction_id == CanonicalTransaction.id).where(or_(
            CanonicalTransaction.store_id.in_(stores),
            (TransactionAdjustment.transaction_id.is_(None)) & (TransactionAdjustment.proposed_values["store_id"].as_string().in_(stores)),
        ))
    if transaction_id:
        query = query.where(TransactionAdjustment.transaction_id == transaction_id)
    if q and q.strip():
        term = q.strip()
        query = query.where(or_(
            contains_literal(TransactionAdjustment.adjustment_type, term),
            contains_literal(TransactionAdjustment.proposed_by, term),
            contains_literal(TransactionAdjustment.rationale, term),
            contains_literal(TransactionAdjustment.transaction_id, term),
            contains_literal(cast(TransactionAdjustment.proposed_values, String), term),
        ))

    counts_query = query.subquery()
    status_rows = (await db.execute(
        select(counts_query.c.status, func.count(), func.sum(
            case((counts_query.c.status == status, 1), else_=0)
        ))
        .select_from(counts_query)
        .group_by(counts_query.c.status)
    )).all()
    status_counts = {row[0]: row[1] for row in status_rows}
    total = sum(row[2] for row in status_rows) if status else sum(status_counts.values())
    if status:
        query = query.where(TransactionAdjustment.status == status)
    rows = (await db.execute(query.order_by(TransactionAdjustment.created_at.desc(), TransactionAdjustment.id.desc())
                             .offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return {"items": [view(row) for row in rows], "total": total,
            "page": page, "pages": max(1, -(-total // page_size)), "page_size": page_size,
            "status_counts": {key: status_counts.get(key, 0) for key in ("Proposed", "Approved", "Rejected")}}

@router.post("")
async def propose_adjustment(body: AdjustmentBody, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance", "it"))):
    if body.adjustment_type not in ALLOWED or not body.rationale.strip() or not body.proposed_values:
        raise HTTPException(status_code=422, detail="A supported adjustment type, proposed values, and rationale are required")
    validate_adjustment_values(body.adjustment_type, body.proposed_values)
    transaction = None
    if body.adjustment_type == "MissingTransaction":
        required = {"business_date", "store_id", "amount", "transaction_type"}
        missing = required - set(body.proposed_values)
        if body.transaction_id or missing:
            raise HTTPException(status_code=422, detail="MissingTransaction requires no transaction_id and values for business_date, store_id, amount, and transaction_type")
        scope.require_store(user, body.proposed_values["store_id"])
    else:
        if not body.transaction_id: raise HTTPException(status_code=422, detail="transaction_id is required for this correction type")
        transaction = (await db.execute(select(CanonicalTransaction).where(CanonicalTransaction.id == body.transaction_id))).scalar_one_or_none()
        if not transaction: raise HTTPException(status_code=404, detail="Source transaction not found")
        scope.require_store(user, transaction.store_id)
    adjustment = TransactionAdjustment(id=str(uuid.uuid4()), transaction_id=transaction.id if transaction else None, adjustment_type=body.adjustment_type, proposed_values=body.proposed_values, rationale=body.rationale.strip(), proposed_by=user["user"] or "ontology.user")
    db.add(adjustment); audit(db, "TRANSACTION_ADJUSTMENT_PROPOSED", None, "TransactionAdjustment", adjustment.id, f"{body.adjustment_type} adjustment proposed", actor=adjustment.proposed_by)
    await db.commit(); return view(adjustment)

@router.post("/{adjustment_id}/approve")
async def approve_adjustment(adjustment_id: str, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance"))):
    adjustment = (await db.execute(select(TransactionAdjustment).where(TransactionAdjustment.id == adjustment_id))).scalar_one_or_none()
    if not adjustment or adjustment.status != "Proposed": raise HTTPException(status_code=409, detail="Proposed adjustment not found")
    validate_adjustment_values(adjustment.adjustment_type, adjustment.proposed_values)
    missing_transaction = None
    if adjustment.adjustment_type == "MissingTransaction":
        amount, transaction_type, business_date, event_at, settlement_date = validate_missing_transaction(
            adjustment.proposed_values
        )
        missing_transaction = (
            amount,
            transaction_type,
            business_date,
            event_at,
            settlement_date,
            missing_transaction_source(adjustment.proposed_values),
        )
    context = await prepare_approval(db, adjustment, user, missing_transaction)
    claimed = (await db.execute(update(TransactionAdjustment).where(
        TransactionAdjustment.id == adjustment_id, TransactionAdjustment.status == "Proposed"
    ).values(status="Approved", reviewed_by=user["user"] or "ontology.user", reviewed_at=utcnow())
        .returning(TransactionAdjustment.id).execution_options(synchronize_session=False))).scalar_one_or_none()
    if claimed is None:
        raise HTTPException(status_code=409, detail="Adjustment was already reviewed")
    await db.refresh(adjustment)
    await apply_approval_effects(db, adjustment, context, user["user"] or "ontology.user")
    audit(db, "TRANSACTION_ADJUSTMENT_APPROVED", None, "TransactionAdjustment", adjustment.id, "Transaction adjustment approved", actor=adjustment.reviewed_by, after={"transaction_id": adjustment.transaction_id})
    await db.commit(); return view(adjustment)

@router.post("/{adjustment_id}/reject")
async def reject_adjustment(adjustment_id: str, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance"))):
    adjustment = (await db.execute(select(TransactionAdjustment).where(TransactionAdjustment.id == adjustment_id))).scalar_one_or_none()
    if not adjustment or adjustment.status != "Proposed": raise HTTPException(status_code=409, detail="Proposed adjustment not found")
    if adjustment.transaction_id:
        transaction = await db.get(CanonicalTransaction, adjustment.transaction_id)
        if not transaction:
            raise HTTPException(status_code=409, detail="Adjustment transaction no longer exists")
        store_id = transaction.store_id
    else:
        store_id = adjustment.proposed_values.get("store_id")
    scope.require_store(user, store_id)
    claimed = (await db.execute(update(TransactionAdjustment).where(
        TransactionAdjustment.id == adjustment_id, TransactionAdjustment.status == "Proposed"
    ).values(status="Rejected", reviewed_by=user["user"] or "ontology.user", reviewed_at=utcnow())
        .returning(TransactionAdjustment.id).execution_options(synchronize_session=False))).scalar_one_or_none()
    if claimed is None:
        raise HTTPException(status_code=409, detail="Adjustment was already reviewed")
    await db.refresh(adjustment)
    audit(db, "TRANSACTION_ADJUSTMENT_REJECTED", None, "TransactionAdjustment", adjustment.id, "Transaction adjustment rejected", actor=adjustment.reviewed_by)
    await db.commit(); return view(adjustment)
