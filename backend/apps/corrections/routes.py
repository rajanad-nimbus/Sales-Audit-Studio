"""Governed transaction correction proposals and approvals."""

import json
import uuid
from decimal import Decimal
from typing import Annotated, Literal

from database import get_db
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from integrations.ontology.nimbus import audit
from model_base import utcnow
from pydantic import BaseModel, Field
from sqlalchemy import String, case, cast, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.corrections.approval import apply_approval_effects, prepare_approval
from apps.corrections.models import TransactionAdjustment
from apps.corrections.validation import (
    missing_transaction_detail,
    missing_transaction_source,
    validate_adjustment_values,
    validate_missing_transaction,
)
from apps.csv_export import csv_row
from apps.search import contains_literal
from apps.security import scope
from apps.security.authentication import require_role
from apps.security.sensitive_data import mask_pan_value, redact_sensitive_fields
from apps.store_days.freshness import lock_store_day
from apps.system.options import credit_card_number_mask
from apps.transactions.customer_attributes import (
    redact_customer_attribute_value,
    redact_customer_attributes,
)
from apps.transactions.models import (
    CanonicalTransaction,
    TransactionDiscount,
    TransactionLine,
    TransactionTax,
    TransactionTender,
)
from apps.transactions.views import mask_customer_id

router = APIRouter(prefix="/api/transaction-adjustments", tags=["Transaction corrections"])
CORRECTION_CSV_MAX_ROWS = 50_000
ALLOWED = {
    "Amount",
    "Tender",
    "Tax",
    "TaxDetail",
    "TaxLine",
    "Discount",
    "Reference",
    "Item",
    "Customer",
    "Employee",
    "CustomerAttribute",
    "UserReference",
    "MissingTransaction",
}


class AdjustmentBody(BaseModel):
    transaction_id: str | None = Field(default=None, min_length=1, max_length=36)
    adjustment_type: str = Field(min_length=1, max_length=50)
    proposed_values: dict = Field(min_length=1, max_length=20)
    rationale: str = Field(min_length=1, max_length=5000)


def view(row: TransactionAdjustment):
    proposed_values = dict(row.proposed_values)
    for field in ("customer_id", "before_customer_id"):
        if proposed_values.get(field) is not None:
            proposed_values[field] = mask_customer_id(str(proposed_values[field]))
    if row.adjustment_type == "UserReference":
        reference_field = proposed_values.get("reference_field")
        if reference_field in {"reference_1", "reference_2", "reference_3", "reference_4"}:
            proposed_values["value"] = mask_pan_value(proposed_values.get("value"))
            proposed_values["before_value"] = mask_pan_value(proposed_values.get("before_value"))
    if row.adjustment_type == "CustomerAttribute":
        attribute_type = proposed_values.get("attribute_type")
        if attribute_type:
            proposed_values = {
                **proposed_values,
                **{
                    key: redact_customer_attribute_value(attribute_type, proposed_values.get(key))
                    for key in ("value", "before_value")
                },
            }
    return {
        "id": row.id,
        "transaction_id": row.transaction_id,
        "adjustment_type": row.adjustment_type,
        "proposed_values": proposed_values,
        "rationale": row.rationale,
        "status": row.status,
        "proposed_by": row.proposed_by,
        "reviewed_by": row.reviewed_by,
        "created_at": row.created_at,
    }


def transaction_view(row: CanonicalTransaction):
    return {
        "id": row.id,
        "business_date": row.business_date,
        "transaction_type": row.transaction_type,
        "signed_amount": row.signed_amount,
        "currency": row.currency,
        "store_id": row.store_id,
        "customer_id": mask_customer_id(row.customer_id),
        "customer_identification_type": row.customer_identification_type,
        "cashier_id": row.cashier_id,
        "salesperson_id": row.salesperson_id,
        "customer_attributes": redact_customer_attributes(row.customer_attributes or {}),
        "user_references": redact_sensitive_fields(row.user_references or {}),
        "register_id": row.register_id,
        "tender_type": row.tender_type,
        "payment_reference": row.payment_reference,
        "settlement_reference": row.settlement_reference,
        "source_lineage": row.source_lineage,
        "reconciliation_status": row.reconciliation_status,
    }


@router.get("/transactions/search")
async def search_transactions(
    q: Annotated[str, Query(min_length=2, max_length=255)],
    limit: Annotated[int, Query(ge=1, le=25)] = 12,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it")),
):
    term = q.strip()
    if len(term) < 2:
        raise HTTPException(
            status_code=422, detail="Enter at least two characters to search transactions"
        )
    query = scope.restrict(select(CanonicalTransaction), user, CanonicalTransaction.store_id)
    rows = (
        (
            await db.execute(
                query.where(
                    or_(
                        contains_literal(CanonicalTransaction.id, term),
                        contains_literal(CanonicalTransaction.payment_reference, term),
                        contains_literal(CanonicalTransaction.settlement_reference, term),
                        contains_literal(CanonicalTransaction.store_id, term),
                    )
                )
                .order_by(CanonicalTransaction.business_date.desc())
                .limit(min(limit, 25))
            )
        )
        .scalars()
        .all()
    )
    return [transaction_view(row) for row in rows]


@router.get("/transactions/facets")
async def transaction_facets(
    db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance", "it"))
):
    stores = (
        (
            await db.execute(
                scope.restrict(
                    select(CanonicalTransaction.store_id), user, CanonicalTransaction.store_id
                )
                .distinct()
                .order_by(CanonicalTransaction.store_id)
            )
        )
        .scalars()
        .all()
    )
    tenders = (
        (
            await db.execute(
                scope.restrict(
                    select(CanonicalTransaction.tender_type), user, CanonicalTransaction.store_id
                )
                .where(CanonicalTransaction.tender_type.is_not(None))
                .distinct()
                .order_by(CanonicalTransaction.tender_type)
            )
        )
        .scalars()
        .all()
    )
    types = (
        (
            await db.execute(
                scope.restrict(
                    select(CanonicalTransaction.transaction_type),
                    user,
                    CanonicalTransaction.store_id,
                )
                .distinct()
                .order_by(CanonicalTransaction.transaction_type)
            )
        )
        .scalars()
        .all()
    )
    return {"stores": stores, "tenders": tenders, "transaction_types": types}


@router.get("/transactions/{transaction_id}")
async def get_transaction(
    transaction_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it")),
):
    row = (
        await db.execute(
            select(CanonicalTransaction).where(CanonicalTransaction.id == transaction_id)
        )
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Canonical transaction not found")
    scope.require_store(user, row.store_id)
    return transaction_view(row)


@router.get("")
async def list_adjustments(
    transaction_id: str | None = None,
    adjustment_id: Annotated[str | None, Query(min_length=1, max_length=36)] = None,
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it")),
    page: Annotated[int, Query(ge=1, le=10000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    q: Annotated[str | None, Query(max_length=255)] = None,
    format: Literal["json", "csv"] = "json",
):
    """Return a bounded corrections page, accurate counts and optional text filtering."""
    if status is not None and status not in {"Proposed", "Approved", "Rejected"}:
        raise HTTPException(
            status_code=422, detail="status must be Proposed, Approved, or Rejected"
        )
    query = select(TransactionAdjustment)
    stores = scope.allowed_stores(user)
    if stores is not None:
        query = query.outerjoin(
            CanonicalTransaction, TransactionAdjustment.transaction_id == CanonicalTransaction.id
        ).where(
            or_(
                CanonicalTransaction.store_id.in_(stores),
                (TransactionAdjustment.transaction_id.is_(None))
                & (TransactionAdjustment.proposed_values["store_id"].as_string().in_(stores)),
            )
        )
    if transaction_id:
        query = query.where(TransactionAdjustment.transaction_id == transaction_id)
    if adjustment_id:
        query = query.where(TransactionAdjustment.id == adjustment_id)
    if q and q.strip():
        term = q.strip()
        query = query.where(
            or_(
                contains_literal(TransactionAdjustment.adjustment_type, term),
                contains_literal(TransactionAdjustment.proposed_by, term),
                contains_literal(TransactionAdjustment.rationale, term),
                contains_literal(TransactionAdjustment.transaction_id, term),
                contains_literal(cast(TransactionAdjustment.proposed_values, String), term),
            )
        )

    if format == "csv":
        if status:
            query = query.where(TransactionAdjustment.status == status)
        total = await db.scalar(select(func.count()).select_from(query.subquery()))
        columns = (
            "created_at",
            "adjustment_type",
            "transaction_id",
            "proposed_by",
            "reviewed_by",
            "status",
            "rationale",
            "proposed_values",
        )

        async def generate():
            yield csv_row(columns)
            result = await db.stream(
                query.order_by(
                    TransactionAdjustment.created_at.desc(), TransactionAdjustment.id.desc()
                )
                .limit(CORRECTION_CSV_MAX_ROWS)
                .execution_options(yield_per=1000)
            )
            try:
                async for row in result.scalars():
                    record = view(row)
                    yield csv_row(
                        (
                            record["created_at"].isoformat(),
                            record["adjustment_type"],
                            record["transaction_id"],
                            record["proposed_by"],
                            record["reviewed_by"],
                            record["status"],
                            record["rationale"],
                            json.dumps(
                                record["proposed_values"], sort_keys=True, ensure_ascii=False
                            ),
                        )
                    )
            finally:
                await result.close()

        return StreamingResponse(
            generate(),
            media_type="text/csv",
            headers={
                "Content-Disposition": "attachment; filename=transaction-corrections.csv",
                "X-Corrections-Truncated": "true" if total > CORRECTION_CSV_MAX_ROWS else "false",
            },
        )

    counts_query = query.subquery()
    status_rows = (
        await db.execute(
            select(
                counts_query.c.status,
                func.count(),
                func.sum(case((counts_query.c.status == status, 1), else_=0)),
            )
            .select_from(counts_query)
            .group_by(counts_query.c.status)
        )
    ).all()
    status_counts = {row[0]: row[1] for row in status_rows}
    total = sum(row[2] for row in status_rows) if status else sum(status_counts.values())
    if status:
        query = query.where(TransactionAdjustment.status == status)
    rows = (
        (
            await db.execute(
                query.order_by(
                    TransactionAdjustment.created_at.desc(), TransactionAdjustment.id.desc()
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [view(row) for row in rows],
        "total": total,
        "page": page,
        "pages": max(1, -(-total // page_size)),
        "page_size": page_size,
        "status_counts": {
            key: status_counts.get(key, 0) for key in ("Proposed", "Approved", "Rejected")
        },
    }


@router.post("")
async def propose_adjustment(
    body: AdjustmentBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it")),
):
    if (
        body.adjustment_type not in ALLOWED
        or not body.rationale.strip()
        or not body.proposed_values
    ):
        raise HTTPException(
            status_code=422,
            detail="A supported adjustment type, proposed values, and rationale are required",
        )
    validate_adjustment_values(body.adjustment_type, body.proposed_values)
    mask_character = await credit_card_number_mask(db)
    stored_values = redact_sensitive_fields(body.proposed_values, mask_character)
    if body.adjustment_type == "MissingTransaction" and "customer_attributes" in stored_values:
        stored_values["customer_attributes"] = redact_customer_attributes(
            stored_values["customer_attributes"], mask_character
        )
    if body.adjustment_type == "CustomerAttribute":
        attribute_type = stored_values["attribute_type"]
        for key in ("value", "before_value"):
            stored_values[key] = redact_customer_attribute_value(
                attribute_type, stored_values.get(key), mask_character
            )
    transaction = None
    if body.adjustment_type == "MissingTransaction":
        required = {"business_date", "store_id", "amount", "transaction_type"}
        missing = required - set(body.proposed_values)
        if body.transaction_id or missing:
            raise HTTPException(
                status_code=422,
                detail="MissingTransaction requires no transaction_id and values for business_date, store_id, amount, and transaction_type",
            )
        store_id = body.proposed_values["store_id"]
        business_date = body.proposed_values["business_date"]
        scope.require_store(user, store_id)
        register_id = body.proposed_values.get("register_id")
        transaction_number = body.proposed_values.get("transaction_number")
        if register_id is not None and transaction_number is not None:
            # Serialize proposals for the same store day so two reviewers cannot create
            # competing missing-transaction approvals for one register number.
            await lock_store_day(db, store_id, business_date)
            pending = await db.scalar(
                select(TransactionAdjustment.id)
                .where(
                    TransactionAdjustment.adjustment_type == "MissingTransaction",
                    TransactionAdjustment.status == "Proposed",
                    TransactionAdjustment.proposed_values["store_id"].as_string() == store_id,
                    TransactionAdjustment.proposed_values["business_date"].as_string()
                    == business_date,
                    TransactionAdjustment.proposed_values["register_id"].as_string() == register_id,
                    TransactionAdjustment.proposed_values["transaction_number"].as_string()
                    == transaction_number,
                )
                .limit(1)
            )
            if pending is not None:
                raise HTTPException(
                    status_code=409,
                    detail="A missing-transaction correction is already awaiting approval for this register number",
                )
    else:
        if not body.transaction_id:
            raise HTTPException(
                status_code=422, detail="transaction_id is required for this correction type"
            )
        transaction = (
            await db.execute(
                select(CanonicalTransaction).where(CanonicalTransaction.id == body.transaction_id)
            )
        ).scalar_one_or_none()
        if not transaction:
            raise HTTPException(status_code=404, detail="Source transaction not found")
        scope.require_store(user, transaction.store_id)
        if body.adjustment_type == "Item":
            line_no = body.proposed_values.get("line_no")
            if line_no is None:
                lines = (
                    (
                        await db.execute(
                            select(TransactionLine)
                            .where(TransactionLine.transaction_id == transaction.id)
                            .order_by(TransactionLine.line_no)
                            .limit(2)
                        )
                    )
                    .scalars()
                    .all()
                )
                if len(lines) != 1:
                    raise HTTPException(
                        status_code=422,
                        detail="Select an item line to correct when the transaction has multiple items",
                    )
                line = lines[0]
            else:
                line = (
                    await db.execute(
                        select(TransactionLine).where(
                            TransactionLine.transaction_id == transaction.id,
                            TransactionLine.line_no == line_no,
                        )
                    )
                ).scalar_one_or_none()
                if line is None:
                    raise HTTPException(status_code=404, detail="Transaction item line not found")
            stored_values["line_no"] = line.line_no
            stored_values["before_item_code"] = line.item_code
        elif body.adjustment_type == "TaxLine":
            line_no = stored_values["line_no"]
            line = (
                await db.execute(
                    select(TransactionLine).where(
                        TransactionLine.transaction_id == transaction.id,
                        TransactionLine.line_no == line_no,
                    )
                )
            ).scalar_one_or_none()
            if line is None:
                raise HTTPException(status_code=404, detail="Transaction item line not found")
            stored_values["tax_code"] = stored_values["tax_code"] or None
            for field, attribute in (
                ("tax_code", "tax_code"),
                ("rate", "tax_rate"),
                ("tax_amount", "tax_amount"),
            ):
                stored_values[f"before_{field}"] = (
                    getattr(line, attribute)
                    if field == "tax_code"
                    else str(getattr(line, attribute))
                )
            unchanged = (
                stored_values["tax_code"] == stored_values["before_tax_code"]
                and Decimal(str(stored_values["rate"]))
                == Decimal(str(stored_values["before_rate"]))
                and Decimal(str(stored_values["tax_amount"]))
                == Decimal(str(stored_values["before_tax_amount"]))
            )
            if unchanged:
                raise HTTPException(status_code=422, detail="The line tax is unchanged")
        elif body.adjustment_type == "Discount":
            operation = stored_values.get("operation", "update")
            stored_values["operation"] = operation
            if operation != "delete" and stored_values["line_no"] is not None:
                line_exists = await db.scalar(
                    select(TransactionLine.id).where(
                        TransactionLine.transaction_id == transaction.id,
                        TransactionLine.line_no == stored_values["line_no"],
                    )
                )
                if line_exists is None:
                    raise HTTPException(status_code=422, detail="Discount item line not found")
            if operation == "add":
                after_amount = Decimal(str(stored_values["amount"]))
                if after_amount == 0:
                    raise HTTPException(
                        status_code=422, detail="A new discount amount must not be zero"
                    )
                if transaction.signed_amount == 0 or (
                    (after_amount > 0) != (transaction.signed_amount > 0)
                ):
                    raise HTTPException(
                        status_code=422,
                        detail="Discount amount must use the transaction's financial sign",
                    )
            else:
                discount = (
                    await db.execute(
                        select(TransactionDiscount).where(
                            TransactionDiscount.id == stored_values["discount_id"],
                            TransactionDiscount.transaction_id == transaction.id,
                        )
                    )
                ).scalar_one_or_none()
                if discount is None:
                    raise HTTPException(status_code=404, detail="Transaction discount not found")
                stored_values.update(
                    {
                        "before_kind": discount.kind,
                        "before_code": discount.code,
                        "before_description": discount.description,
                        "before_line_no": discount.line_no,
                        "before_amount": str(discount.amount),
                    }
                )
                if operation == "update":
                    before_amount = Decimal(str(discount.amount))
                    after_amount = Decimal(str(stored_values["amount"]))
                    if after_amount != 0 and (
                        transaction.signed_amount == 0
                        or (after_amount > 0) != (transaction.signed_amount > 0)
                    ):
                        raise HTTPException(
                            status_code=422,
                            detail="Discount amount must use the transaction's financial sign",
                        )
                else:
                    before_amount = Decimal(str(discount.amount))
                    after_amount = before_amount
            if operation == "update" and (
                all(
                    getattr(discount, field) == stored_values[field]
                    for field in ("kind", "code", "description", "line_no")
                )
                and before_amount == after_amount
            ):
                raise HTTPException(status_code=422, detail="The discount is unchanged")
        elif body.adjustment_type == "Customer":
            stored_values["before_customer_id"] = transaction.customer_id
            stored_values["before_customer_identification_type"] = (
                transaction.customer_identification_type
            )
        elif body.adjustment_type == "Employee":
            changed = False
            for field in ("cashier_id", "salesperson_id"):
                if field in stored_values:
                    current = getattr(transaction, field)
                    stored_values[f"before_{field}"] = current
                    changed = changed or stored_values[field] != current
            if not changed:
                raise HTTPException(status_code=422, detail="Employee details are unchanged")
        elif body.adjustment_type == "CustomerAttribute":
            current_attributes = (
                redact_customer_attributes(transaction.customer_attributes or {}, mask_character)
                or {}
            )
            before_value = current_attributes.get(stored_values["attribute_type"])
            if before_value == stored_values["value"]:
                raise HTTPException(status_code=422, detail="The customer attribute is unchanged")
            stored_values["before_value"] = before_value
        elif body.adjustment_type == "TaxDetail":
            tax_line = (
                await db.execute(
                    select(TransactionTax).where(
                        TransactionTax.id == body.proposed_values["tax_id"],
                        TransactionTax.transaction_id == transaction.id,
                    )
                )
            ).scalar_one_or_none()
            if tax_line is None:
                raise HTTPException(status_code=404, detail="Transaction tax row not found")
            for field in ("tax_code", "tax_name", "rate", "taxable_amount", "tax_amount"):
                stored_values[f"before_{field}"] = str(getattr(tax_line, field))
        elif body.adjustment_type == "UserReference":
            reference_field = stored_values["reference_field"]
            stored_values["value"] = mask_pan_value(stored_values["value"], mask_character)
            stored_values["before_value"] = mask_pan_value(
                (transaction.user_references or {}).get(reference_field), mask_character
            )
        elif body.adjustment_type in {"Tender", "Reference"}:
            tender_id = body.proposed_values.get("tender_id")
            tender_rows = (
                (
                    await db.execute(
                        select(TransactionTender)
                        .where(TransactionTender.transaction_id == transaction.id)
                        .order_by(TransactionTender.id)
                    )
                )
                .scalars()
                .all()
            )
            if tender_id is None and len(tender_rows) > 1:
                raise HTTPException(
                    status_code=422,
                    detail="Select a tender entry when the transaction has multiple tenders",
                )
            tender_line = None
            if tender_id is not None:
                tender_line = next((row for row in tender_rows if row.id == tender_id), None)
                if tender_line is None:
                    raise HTTPException(status_code=404, detail="Transaction tender not found")
            elif len(tender_rows) == 1:
                tender_line = tender_rows[0]
            if tender_line is not None:
                stored_values["tender_id"] = tender_line.id
                stored_values["updates_header"] = len(tender_rows) == 1
            else:
                stored_values["updates_header"] = True
            stored_values["tender_count"] = len(tender_rows)
            if body.adjustment_type == "Tender":
                stored_values["before_tender_type"] = (
                    tender_line.tender_type if tender_line else transaction.tender_type
                )
            else:
                stored_values["before_reference"] = mask_pan_value(
                    tender_line.reference if tender_line else transaction.payment_reference,
                    mask_character,
                )
    adjustment = TransactionAdjustment(
        id=str(uuid.uuid4()),
        transaction_id=transaction.id if transaction else None,
        adjustment_type=body.adjustment_type,
        proposed_values=stored_values,
        rationale=body.rationale.strip(),
        proposed_by=user["user"] or "ontology.user",
    )
    db.add(adjustment)
    audit(
        db,
        "TRANSACTION_ADJUSTMENT_PROPOSED",
        None,
        "TransactionAdjustment",
        adjustment.id,
        f"{body.adjustment_type} adjustment proposed",
        actor=adjustment.proposed_by,
    )
    await db.commit()
    return view(adjustment)


@router.post("/{adjustment_id}/approve")
async def approve_adjustment(
    adjustment_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance")),
):
    adjustment = (
        await db.execute(
            select(TransactionAdjustment).where(TransactionAdjustment.id == adjustment_id)
        )
    ).scalar_one_or_none()
    if not adjustment or adjustment.status != "Proposed":
        raise HTTPException(status_code=409, detail="Proposed adjustment not found")
    validate_adjustment_values(adjustment.adjustment_type, adjustment.proposed_values)
    missing_transaction = None
    if adjustment.adjustment_type == "MissingTransaction":
        amount, transaction_type, business_date, event_at, settlement_date = (
            validate_missing_transaction(adjustment.proposed_values)
        )
        missing_transaction = (
            amount,
            transaction_type,
            business_date,
            event_at,
            settlement_date,
            missing_transaction_source(adjustment.proposed_values),
            missing_transaction_detail(adjustment.proposed_values),
        )
    context = await prepare_approval(db, adjustment, user, missing_transaction)
    claimed = (
        await db.execute(
            update(TransactionAdjustment)
            .where(
                TransactionAdjustment.id == adjustment_id,
                TransactionAdjustment.status == "Proposed",
            )
            .values(
                status="Approved", reviewed_by=user["user"] or "ontology.user", reviewed_at=utcnow()
            )
            .returning(TransactionAdjustment.id)
            .execution_options(synchronize_session=False)
        )
    ).scalar_one_or_none()
    if claimed is None:
        raise HTTPException(status_code=409, detail="Adjustment was already reviewed")
    await db.refresh(adjustment)
    await apply_approval_effects(db, adjustment, context, user["user"] or "ontology.user")
    audit(
        db,
        "TRANSACTION_ADJUSTMENT_APPROVED",
        None,
        "TransactionAdjustment",
        adjustment.id,
        "Transaction adjustment approved",
        actor=adjustment.reviewed_by,
        after={
            "transaction_id": adjustment.transaction_id,
            **(
                {
                    "line_no": adjustment.proposed_values.get("line_no"),
                    "before_item_code": adjustment.proposed_values.get("before_item_code"),
                    "item_code": adjustment.proposed_values.get("item"),
                }
                if adjustment.adjustment_type == "Item"
                else {}
            ),
            **(
                {
                    "operation": adjustment.proposed_values.get("operation", "update"),
                    "discount_id": adjustment.proposed_values.get("discount_id")
                    or adjustment.proposed_values.get("created_discount_id"),
                    **(
                        {
                            f"before_{field}": adjustment.proposed_values.get(f"before_{field}")
                            for field in ("kind", "code", "description", "line_no", "amount")
                        }
                        if adjustment.proposed_values.get("operation", "update")
                        in {"update", "delete"}
                        else {
                            f"before_{field}": None
                            for field in ("kind", "code", "description", "line_no", "amount")
                        }
                    ),
                    **(
                        {
                            f"after_{field}": adjustment.proposed_values.get(field)
                            for field in ("kind", "code", "description", "line_no", "amount")
                        }
                        if adjustment.proposed_values.get("operation", "update") != "delete"
                        else {
                            f"after_{field}": None
                            for field in ("kind", "code", "description", "line_no", "amount")
                        }
                    ),
                }
                if adjustment.adjustment_type == "Discount"
                else {}
            ),
            **(
                {
                    "line_no": adjustment.proposed_values.get("line_no"),
                    **{
                        f"before_{field}": adjustment.proposed_values.get(f"before_{field}")
                        for field in ("tax_code", "rate", "tax_amount")
                    },
                    **{
                        f"after_{field}": adjustment.proposed_values.get(field)
                        for field in ("tax_code", "rate", "tax_amount")
                    },
                }
                if adjustment.adjustment_type == "TaxLine"
                else {}
            ),
            **(
                {
                    "tax_id": adjustment.proposed_values.get("tax_id"),
                    **{
                        field: adjustment.proposed_values.get(f"before_{field}")
                        for field in (
                            "tax_code",
                            "tax_name",
                            "rate",
                            "taxable_amount",
                            "tax_amount",
                        )
                    },
                    **{
                        f"after_{field}": adjustment.proposed_values.get(field)
                        for field in (
                            "tax_code",
                            "tax_name",
                            "rate",
                            "taxable_amount",
                            "tax_amount",
                        )
                    },
                }
                if adjustment.adjustment_type == "TaxDetail"
                else {}
            ),
            **(
                {
                    "tender_id": adjustment.proposed_values.get("tender_id"),
                    "before": adjustment.proposed_values.get(
                        "before_tender_type"
                        if adjustment.adjustment_type == "Tender"
                        else "before_reference"
                    ),
                    "after": adjustment.proposed_values.get(
                        "tender_type" if adjustment.adjustment_type == "Tender" else "reference"
                    ),
                }
                if adjustment.adjustment_type in {"Tender", "Reference"}
                else {}
            ),
            **(
                {
                    **(
                        {
                            "before_customer_id": mask_customer_id(
                                adjustment.proposed_values.get("before_customer_id")
                            ),
                            "customer_id": mask_customer_id(
                                adjustment.proposed_values.get("customer_id")
                            ),
                        }
                        if "customer_id" in adjustment.proposed_values
                        else {}
                    ),
                    **(
                        {
                            "before_customer_identification_type": adjustment.proposed_values.get(
                                "before_customer_identification_type"
                            ),
                            "customer_identification_type": adjustment.proposed_values.get(
                                "customer_identification_type"
                            ),
                        }
                        if "customer_identification_type" in adjustment.proposed_values
                        else {}
                    ),
                }
                if adjustment.adjustment_type == "Customer"
                else {}
            ),
            **(
                {
                    key: value
                    for field in ("cashier_id", "salesperson_id")
                    if field in adjustment.proposed_values
                    for key, value in (
                        (f"before_{field}", adjustment.proposed_values.get(f"before_{field}")),
                        (field, adjustment.proposed_values.get(field)),
                    )
                }
                if adjustment.adjustment_type == "Employee"
                else {}
            ),
            **(
                {
                    "reference_field": adjustment.proposed_values.get("reference_field"),
                    "before_value": mask_pan_value(adjustment.proposed_values.get("before_value")),
                    "value": mask_pan_value(adjustment.proposed_values.get("value")),
                }
                if adjustment.adjustment_type == "UserReference"
                else {}
            ),
            **(
                {
                    "attribute_type": adjustment.proposed_values.get("attribute_type"),
                    "before_value": "[REDACTED]"
                    if adjustment.proposed_values.get("before_value") is not None
                    else None,
                    "value": "[REDACTED]" if adjustment.proposed_values.get("value") else "",
                }
                if adjustment.adjustment_type == "CustomerAttribute"
                else {}
            ),
        },
    )
    await db.commit()
    return view(adjustment)


@router.post("/{adjustment_id}/reject")
async def reject_adjustment(
    adjustment_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance")),
):
    adjustment = (
        await db.execute(
            select(TransactionAdjustment).where(TransactionAdjustment.id == adjustment_id)
        )
    ).scalar_one_or_none()
    if not adjustment or adjustment.status != "Proposed":
        raise HTTPException(status_code=409, detail="Proposed adjustment not found")
    if adjustment.transaction_id:
        transaction = await db.get(CanonicalTransaction, adjustment.transaction_id)
        if not transaction:
            raise HTTPException(status_code=409, detail="Adjustment transaction no longer exists")
        store_id = transaction.store_id
    else:
        store_id = adjustment.proposed_values.get("store_id")
    scope.require_store(user, store_id)
    claimed = (
        await db.execute(
            update(TransactionAdjustment)
            .where(
                TransactionAdjustment.id == adjustment_id,
                TransactionAdjustment.status == "Proposed",
            )
            .values(
                status="Rejected", reviewed_by=user["user"] or "ontology.user", reviewed_at=utcnow()
            )
            .returning(TransactionAdjustment.id)
            .execution_options(synchronize_session=False)
        )
    ).scalar_one_or_none()
    if claimed is None:
        raise HTTPException(status_code=409, detail="Adjustment was already reviewed")
    await db.refresh(adjustment)
    audit(
        db,
        "TRANSACTION_ADJUSTMENT_REJECTED",
        None,
        "TransactionAdjustment",
        adjustment.id,
        "Transaction adjustment rejected",
        actor=adjustment.reviewed_by,
    )
    await db.commit()
    return view(adjustment)
