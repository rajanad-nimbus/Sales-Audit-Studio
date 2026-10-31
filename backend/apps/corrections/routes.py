"""Governed transaction correction proposals and approvals."""
import hashlib
import json
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import String, case, cast, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_role
from apps.search import contains_literal
from apps.security import scope
from apps.ingest.common import LINEAGE, SIGN, VALID_TYPES
from apps.store_days.freshness import invalidate_totals, lock_store_day
from apps.totals.currency import validate_currency
from apps.corrections.effects import effective_amounts
from apps.corrections.reconciliation import invalidate_case_evidence, linked_cases, related_exception_ids, requeue_linked_exceptions
from apps.transactions.detail import MAX_MONEY, cent_value, money
from database import get_db
from models import CanonicalTransaction, SourceRecord, TransactionAdjustment, utcnow
from integrations.ontology.nimbus import audit

router = APIRouter(prefix="/api/transaction-adjustments", tags=["Transaction corrections"])
ALLOWED = {"Amount", "Tender", "Tax", "Reference", "Item", "MissingTransaction"}

class AdjustmentBody(BaseModel):
    transaction_id: str | None = Field(default=None, min_length=1, max_length=36)
    adjustment_type: str = Field(min_length=1, max_length=50)
    proposed_values: dict = Field(min_length=1, max_length=20)
    rationale: str = Field(min_length=1, max_length=5000)


def validate_adjustment_values(adjustment_type: str, values: dict) -> None:
    """Ensure correction fields match their type before they enter financial calculations."""
    try:
        if adjustment_type == "MissingTransaction":
            validate_missing_transaction(values)
            return
        fields = {
            "Amount": {"amount_delta", "corrected_amount"},
            "Tax": {"tax_delta"},
            "Tender": {"tender_type"},
            "Reference": {"reference"},
            "Item": {"item"},
        }
        allowed = fields.get(adjustment_type)
        if not isinstance(values, dict) or not allowed or len(values) != 1 or not set(values) <= allowed:
            raise ValueError("provide exactly one field appropriate to the adjustment type")
        field, value = next(iter(values.items()))
        if adjustment_type in {"Amount", "Tax"}:
            amount = cent_value(value, field)
            if abs(amount) > MAX_MONEY:
                raise ValueError(f"{field} must fit supported monetary precision")
        elif not isinstance(value, str) or not value.strip() or len(value) > 255:
            raise ValueError(f"{field} must be a nonempty string of at most 255 characters")
    except (ValueError, TypeError, InvalidOperation) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid adjustment values: {exc}") from exc


def validate_missing_transaction(values: dict) -> tuple[Decimal, str, str, datetime, str]:
    """Validate all fields needed to safely create an approved missing transaction."""
    try:
        allowed = {"amount", "business_date", "store_id", "transaction_type", "event_timestamp",
                   "settlement_date", "currency", "quantity", "register_id", "channel_id", "tender_type",
                   "payment_reference", "settlement_reference", "source_system"}
        if not isinstance(values, dict) or set(values) - allowed:
            raise ValueError("unsupported missing transaction fields")
        amount = money(values["amount"], "amount", allow_zero=False)
        business_date = str(values["business_date"])
        if date.fromisoformat(business_date).isoformat() != business_date:
            raise ValueError("business_date must use YYYY-MM-DD")
        store = values["store_id"]
        if not isinstance(store, str) or not store.strip() or len(store) > 50 or store != store.strip():
            raise ValueError("store_id must contain 1 to 50 characters without surrounding whitespace")
        transaction_type = values["transaction_type"]
        if not isinstance(transaction_type, str) or transaction_type not in SIGN:
            raise ValueError("unsupported transaction_type")
        missing_transaction_source(values)
        event_at = datetime.fromisoformat(str(values.get("event_timestamp") or f"{business_date}T00:00:00+00:00").replace("Z", "+00:00"))
        if event_at.tzinfo is None:
            event_at = event_at.replace(tzinfo=timezone.utc)
        settlement_date = str(values.get("settlement_date") or business_date)
        if date.fromisoformat(settlement_date).isoformat() != settlement_date:
            raise ValueError("settlement_date must use YYYY-MM-DD")
        currency = values.get("currency") or "USD"
        validate_currency(currency)
        quantity = values.get("quantity")
        if quantity is not None and (isinstance(quantity, bool) or not isinstance(quantity, int)
                                     or not -(2**31) <= quantity < 2**31):
            raise ValueError("quantity must be a 32-bit integer")
        for name, max_length in (("register_id", 50), ("channel_id", 50), ("tender_type", 50),
                                 ("payment_reference", 255), ("settlement_reference", 255)):
            value = values.get(name)
            if value is not None and (not isinstance(value, str) or len(value) > max_length):
                raise ValueError(f"{name} must be a string of at most {max_length} characters")
        return amount, transaction_type, business_date, event_at, settlement_date
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid missing transaction: {exc}") from exc


def missing_transaction_source(values: dict) -> str:
    """Resolve an unambiguous source, requiring an explicit source for shared types."""
    transaction_type = values["transaction_type"]
    candidates = [source for source, types in VALID_TYPES.items() if transaction_type in types]
    source = values.get("source_system")
    if source is None and len(candidates) == 1:
        return candidates[0]
    if not isinstance(source, str) or source not in candidates:
        raise ValueError("source_system must identify a valid source for the transaction type")
    return source

def view(row: TransactionAdjustment):
    return {"id": row.id, "transaction_id": row.transaction_id, "adjustment_type": row.adjustment_type, "proposed_values": row.proposed_values, "rationale": row.rationale, "status": row.status, "proposed_by": row.proposed_by, "reviewed_by": row.reviewed_by, "created_at": row.created_at}

def transaction_view(row: CanonicalTransaction):
    return {"id": row.id, "business_date": row.business_date, "transaction_type": row.transaction_type,
            "signed_amount": row.signed_amount, "currency": row.currency, "store_id": row.store_id,
            "register_id": row.register_id, "tender_type": row.tender_type, "payment_reference": row.payment_reference,
            "settlement_reference": row.settlement_reference, "source_lineage": row.source_lineage,
            "reconciliation_status": row.reconciliation_status}

@router.get("/transactions/search")
async def search_transactions(q: str, limit: Annotated[int, Query(ge=1, le=25)] = 12, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance", "it"))):
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
    if adjustment.adjustment_type == "MissingTransaction":
        values = adjustment.proposed_values
        amount, transaction_type, business_date, event_at, settlement_date = validate_missing_transaction(values)
        signed = -amount if transaction_type in {"Return", "Refund"} else amount
        store_id = str(values["store_id"])
    else:
        transaction = await db.get(CanonicalTransaction, adjustment.transaction_id)
        if not transaction:
            raise HTTPException(status_code=409, detail="Adjustment transaction no longer exists")
        store_id, business_date = transaction.store_id, transaction.business_date
    scope.require_store(user, store_id)
    day = await lock_store_day(db, store_id, business_date)
    cases = await linked_cases(db, transaction) if adjustment.adjustment_type != "MissingTransaction" else []
    if any(case.status in {"Resolving", "Pending Validation"} for case in cases):
        raise HTTPException(status_code=409, detail="A linked case has an active workflow; resolve it before approving this correction")
    if cases:
        from models import ExportItem

        pending_export = (await db.execute(select(ExportItem.id).where(
            ExportItem.object_id.in_([case.id for case in cases]),
            ExportItem.dataset.in_(["resolutions", "adjustments"]), ExportItem.state == "Pending").limit(1))).scalar_one_or_none()
        if pending_export:
            raise HTTPException(status_code=409, detail="A linked case has a pending export; retry or cancel the batch before approving this correction")
    if adjustment.adjustment_type == "Amount":
        transaction_ids = await related_exception_ids(db, transaction)
        locked = (await db.execute(select(CanonicalTransaction).where(CanonicalTransaction.id.in_(transaction_ids))
                  .order_by(CanonicalTransaction.id).with_for_update().execution_options(populate_existing=True))).scalars().all()
        transaction = next(row for row in locked if row.id == adjustment.transaction_id)
        current = (await effective_amounts(db, [transaction]))[transaction.id]
        values = adjustment.proposed_values
        result = Decimal(str(values["corrected_amount"])) if "corrected_amount" in values else current + Decimal(str(values["amount_delta"]))
        if abs(result) > MAX_MONEY:
            raise HTTPException(status_code=422, detail="Adjusted amount exceeds supported monetary precision")
        expected_sign = SIGN.get(transaction.transaction_type)
        if expected_sign is None or result * expected_sign < 0:
            raise HTTPException(status_code=422, detail="Adjusted amount cannot reverse the transaction's financial sign")
    claimed = (await db.execute(update(TransactionAdjustment).where(
        TransactionAdjustment.id == adjustment_id, TransactionAdjustment.status == "Proposed"
    ).values(status="Approved", reviewed_by=user["user"] or "ontology.user", reviewed_at=utcnow())
        .returning(TransactionAdjustment.id).execution_options(synchronize_session=False))).scalar_one_or_none()
    if claimed is None:
        raise HTTPException(status_code=409, detail="Adjustment was already reviewed")
    await db.refresh(adjustment)
    await invalidate_case_evidence(db, cases, adjustment.id, user["user"] or "ontology.user")
    if adjustment.adjustment_type == "Amount":
        await requeue_linked_exceptions(db, transaction, locked, user["user"] or "ontology.user")
    if day:
        await invalidate_totals(db, day, user["user"] or "ontology.user")
    if adjustment.adjustment_type == "MissingTransaction":
        source_id = str(uuid.uuid4())
        payload = json.dumps({"adjustment_id": adjustment.id, "values": values, "rationale": adjustment.rationale}, sort_keys=True, default=str).encode()
        db.add(SourceRecord(id=source_id, source_system="ManualAdjustment", source_version="1.0", source_record_id=f"adjustment-{adjustment.id}", delivery_id=adjustment.id, payload=payload, payload_hash=hashlib.sha256(payload).hexdigest(), received_at=utcnow(), source_event_time=event_at, status="Processed", extracted_at=utcnow()))
        await db.flush()
        transaction_id = str(uuid.uuid4())
        db.add(CanonicalTransaction(id=transaction_id, business_date=business_date, transaction_date=event_at, event_timestamp=event_at, processing_timestamp=utcnow(), settlement_date=settlement_date, transaction_type=transaction_type, transaction_subtype=None, source_lineage=LINEAGE[missing_transaction_source(values)], currency=str(values.get("currency") or "USD"), signed_amount=signed, quantity=values.get("quantity"), store_id=str(values["store_id"]), register_id=values.get("register_id"), channel_id=str(values.get("channel_id") or "store"), tender_type=values.get("tender_type"), payment_reference=values.get("payment_reference"), settlement_reference=values.get("settlement_reference"), reconciliation_status="Unmatched", source_record_id=source_id, disposition="Open"))
        await db.flush()
        adjustment.transaction_id = transaction_id
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
