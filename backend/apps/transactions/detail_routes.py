"""Detailed transaction, return lineage, and related-record endpoints."""

import json
from decimal import Decimal
from typing import Literal

from database import get_db
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import case as sql_case
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.audit.models import AuditEvent
from apps.cases.lineage import case_creation_mentions_transaction
from apps.cases.models import Case
from apps.cases.models import Exception as DBException
from apps.corrections.history import build_history
from apps.corrections.models import TransactionAdjustment
from apps.csv_export import csv_row
from apps.ingest.models import SourceRecord
from apps.security import scope
from apps.security.authentication import require_role
from apps.security.sensitive_data import (
    mask_pan_value,
    redact_sensitive_fields,
    redact_sensitive_preview,
)
from apps.system.options import credit_card_number_mask
from apps.transactions import detail as detail_math
from apps.transactions.customer_attributes import redact_customer_attributes
from apps.transactions.limits import (
    MAX_PAYLOAD_CHARS as MAX_PAYLOAD_CHARS,
)
from apps.transactions.limits import (
    MAX_RELATED_CASES as MAX_RELATED_CASES,
)
from apps.transactions.limits import (
    MAX_RELATED_RETURNS as MAX_RELATED_RETURNS,
)
from apps.transactions.limits import (
    MAX_RETURN_ITEM_ROWS as MAX_RETURN_ITEM_ROWS,
)
from apps.transactions.limits import (
    MAX_TRANSACTION_ADJUSTMENTS as MAX_TRANSACTION_ADJUSTMENTS,
)
from apps.transactions.limits import (
    MAX_TRANSACTION_DETAIL_EXPORT_ROWS as MAX_TRANSACTION_DETAIL_EXPORT_ROWS,
)
from apps.transactions.limits import (
    MAX_TRANSACTION_DETAIL_ROWS as MAX_TRANSACTION_DETAIL_ROWS,
)
from apps.transactions.models import (
    CanonicalTransaction,
    TransactionDiscount,
    TransactionLine,
    TransactionTax,
    TransactionTender,
)
from apps.transactions.returns import fmt_qty as fmt_qty
from apps.transactions.returns import return_context as return_context
from apps.transactions.views import (
    mask_customer_id,
    paid_out_details as resolve_paid_out_details,
    redact_customer_id_preview,
    redact_customer_ids,
    row_view,
)

router = APIRouter(tags=["Transactions"])


def decode_payload(raw: bytes | None) -> dict:
    if not raw:
        return {"text": None, "truncated": False}
    truncated = len(raw) > MAX_PAYLOAD_CHARS
    text = raw[:MAX_PAYLOAD_CHARS].decode("utf-8", errors="replace")
    if truncated:
        text = redact_customer_id_preview(text)
    try:
        if truncated:
            text = redact_sensitive_preview(text)
        else:
            text = json.dumps(
                redact_sensitive_fields(redact_customer_ids(json.loads(text))),
                indent=2,
                default=str,
            )
    except ValueError:
        text = redact_sensitive_preview(redact_customer_id_preview(text))
    return {
        "text": text[:MAX_PAYLOAD_CHARS],
        "truncated": truncated or len(text) > MAX_PAYLOAD_CHARS,
    }


BRIEF_ROWS = 5
AUDIT_TRAIL_PAGE_SIZE_MAX = 100
MAX_TRANSACTION_ERRORS = 100


@router.get("/{transaction_id}/audit-trail")
async def transaction_audit_trail(
    transaction_id: str,
    page: int = Query(default=1, ge=1, le=100000),
    page_size: int = Query(default=50, ge=1, le=AUDIT_TRAIL_PAGE_SIZE_MAX),
    format: Literal["json", "csv"] = Query(default="json"),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("analyst", "finance", "it", "auditor")),
):
    """Review transaction comments, related case decisions and correction history."""
    tx = await db.get(CanonicalTransaction, transaction_id)
    if tx is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    scope.require_store(user, tx.store_id)

    candidate_case_ids = select(AuditEvent.case_id).where(
        AuditEvent.event_type == "CASE_CREATED", case_creation_mentions_transaction(db, tx.id)
    )
    case_query = scope.restrict(
        select(Case.id).where(Case.id.in_(candidate_case_ids)), user, Case.store_id
    )
    case_ids = list((await db.execute(case_query)).scalars())
    adjustment_ids = list(
        (
            await db.execute(
                select(TransactionAdjustment.id).where(
                    TransactionAdjustment.transaction_id == tx.id
                )
            )
        )
        .scalars()
        .all()
    )
    filters = [(AuditEvent.object_type == "Transaction") & (AuditEvent.object_id == tx.id)]
    if case_ids:
        filters.append(AuditEvent.case_id.in_(case_ids))
    if adjustment_ids:
        filters.append(
            (AuditEvent.object_type == "TransactionAdjustment")
            & AuditEvent.object_id.in_(adjustment_ids)
        )
    base = select(AuditEvent).where(or_(*filters))
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    if format == "csv":
        if total > MAX_TRANSACTION_DETAIL_EXPORT_ROWS:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Audit trail contains {total:,} events; limit is "
                    f"{MAX_TRANSACTION_DETAIL_EXPORT_ROWS:,}."
                ),
            )

        async def generate():
            columns = (
                "event_id",
                "audit_sequence",
                "event_type",
                "actor",
                "object_type",
                "object_id",
                "case_id",
                "description",
                "before",
                "after",
                "created_at",
            )
            yield csv_row(columns)
            stream = await db.stream(
                base.order_by(AuditEvent.seq.desc(), AuditEvent.id.desc()).execution_options(
                    yield_per=500
                )
            )
            try:
                async for event in stream.scalars():
                    safe_states = redact_sensitive_fields(
                        {"before": event.before_state, "after": event.after_state}
                    )
                    yield csv_row(
                        (
                            event.id,
                            event.seq,
                            event.event_type,
                            event.actor,
                            event.object_type,
                            event.object_id,
                            event.case_id,
                            event.action_description,
                            json.dumps(safe_states["before"], ensure_ascii=False, default=str),
                            json.dumps(safe_states["after"], ensure_ascii=False, default=str),
                            event.created_at.isoformat(),
                        )
                    )
            finally:
                await stream.close()

        return StreamingResponse(
            generate(),
            media_type="text/csv",
            headers={
                "Content-Disposition": 'attachment; filename="transaction-audit-trail.csv"'
            },
        )
    rows = (
        (
            await db.execute(
                base.order_by(AuditEvent.seq.desc(), AuditEvent.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return redact_sensitive_fields(
        {
            "transaction_id": tx.id,
            "items": [
                {
                    "id": event.id,
                    "seq": event.seq,
                    "event_type": event.event_type,
                    "actor": event.actor,
                    "object_type": event.object_type,
                    "object_id": event.object_id,
                    "case_id": event.case_id,
                    "description": event.action_description,
                    "before": event.before_state,
                    "after": event.after_state,
                    "created_at": event.created_at,
                }
                for event in rows
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": max(1, -(-total // page_size)),
        }
    )


@router.get("/{transaction_id}/brief")
async def transaction_brief(
    transaction_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("analyst", "finance", "it", "auditor")),
):
    """What an opened row of the transaction list shows: how it was paid, a few lines, the tax.

    The full detail page also loads the source record and related cases; a list row needs only this,
    and only a few of each kind, so it stays small for a transaction with hundreds of lines.
    """
    tx = (
        await db.execute(
            select(CanonicalTransaction).where(CanonicalTransaction.id == transaction_id)
        )
    ).scalar_one_or_none()
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")
    scope.require_store(user, tx.store_id)

    line_count = (
        await db.execute(
            select(func.count())
            .select_from(TransactionLine)
            .where(TransactionLine.transaction_id == tx.id)
        )
    ).scalar_one()
    lines = (
        (
            await db.execute(
                select(TransactionLine)
                .where(TransactionLine.transaction_id == tx.id)
                .order_by(TransactionLine.line_no)
                .limit(BRIEF_ROWS)
            )
        )
        .scalars()
        .all()
    )
    tenders = (
        (
            await db.execute(
                select(TransactionTender)
                .where(TransactionTender.transaction_id == tx.id)
                .order_by(TransactionTender.id)
                .limit(BRIEF_ROWS)
            )
        )
        .scalars()
        .all()
    )
    taxes = (
        (
            await db.execute(
                select(TransactionTax)
                .where(TransactionTax.transaction_id == tx.id)
                .order_by(TransactionTax.tax_code)
                .limit(BRIEF_ROWS)
            )
        )
        .scalars()
        .all()
    )
    discount_count = (
        await db.execute(
            select(func.count())
            .select_from(TransactionDiscount)
            .where(TransactionDiscount.transaction_id == tx.id)
        )
    ).scalar_one()
    corrections = (
        await db.execute(
            select(func.count())
            .select_from(TransactionAdjustment)
            .where(TransactionAdjustment.transaction_id == tx.id)
        )
    ).scalar_one()
    return redact_sensitive_fields(
        {
            "id": tx.id,
            "currency": tx.currency,
            "detail_status": tx.detail_status,
            "original_reference": tx.original_reference,
            "return_reason": tx.return_reason,
            "customer_id": mask_customer_id(tx.customer_id),
            "customer_identification_type": tx.customer_identification_type,
            "customer_attributes": redact_customer_attributes(tx.customer_attributes or {}) or {}
            if user["role"] in {"finance", "it"}
            else {},
            "cashier_id": tx.cashier_id,
            "salesperson_id": tx.salesperson_id,
            "user_references": tx.user_references,
            "transaction_number": tx.transaction_number,
            "line_count": line_count,
            "lines": [
                {
                    "line_no": line.line_no,
                    "item_code": line.item_code,
                    "description": line.description,
                    "quantity": line.quantity,
                    "unit_price": line.unit_price,
                    "original_unit_price": line.original_unit_price,
                    "price_override_reason": line.price_override_reason,
                    "source_attributes": line.source_attributes,
                    "gross_amount": line.gross_amount,
                }
                for line in lines
            ],
            "tenders": [
                {
                    "tender_type": t.tender_type,
                    "amount": t.amount,
                    "reference": t.reference,
                    "authorization": t.authorization,
                    "source_attributes": t.source_attributes,
                }
                for t in tenders
            ],
            "taxes": [
                {
                    "tax_code": t.tax_code,
                    "tax_name": t.tax_name,
                    "rate": t.rate,
                    "tax_amount": t.tax_amount,
                }
                for t in taxes
            ],
            "discount_count": discount_count,
            "corrections": corrections,
        }
    )


@router.get("/{transaction_id}/changes")
async def transaction_changes(
    transaction_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("analyst", "finance", "it", "auditor")),
):
    """Every correction to this transaction with the value before and after, oldest first."""
    tx = (
        await db.execute(
            select(CanonicalTransaction).where(CanonicalTransaction.id == transaction_id)
        )
    ).scalar_one_or_none()
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")
    scope.require_store(user, tx.store_id)
    recorded_tax = (
        await db.execute(
            select(func.coalesce(func.sum(TransactionTax.tax_amount), 0)).where(
                TransactionTax.transaction_id == tx.id
            )
        )
    ).scalar_one()
    adjustments = (
        (
            await db.execute(
                select(TransactionAdjustment)
                .where(TransactionAdjustment.transaction_id == tx.id)
                .order_by(TransactionAdjustment.created_at, TransactionAdjustment.id)
                .limit(MAX_TRANSACTION_ADJUSTMENTS + 1)
            )
        )
        .scalars()
        .all()
    )
    line_items = (
        await db.execute(
            select(TransactionLine.line_no, TransactionLine.item_code)
            .where(TransactionLine.transaction_id == tx.id)
            .order_by(TransactionLine.line_no)
            .limit(10_001)
        )
    ).all()
    initial_items = {str(line_no): item_code for line_no, item_code in line_items}
    discount_rows = (
        (
            await db.execute(
                select(TransactionDiscount)
                .where(TransactionDiscount.transaction_id == tx.id)
                .order_by(TransactionDiscount.id)
                .limit(10_001)
            )
        )
        .scalars()
        .all()
    )
    initial_discounts = {
        row.id: {
            "kind": row.kind,
            "code": row.code,
            "description": row.description,
            "line_no": row.line_no,
            "amount": row.amount,
        }
        for row in discount_rows[:10_000]
    }
    tender_rows = (
        await db.execute(
            select(
                TransactionTender.id,
                TransactionTender.tender_type,
                TransactionTender.reference,
            )
            .where(TransactionTender.transaction_id == tx.id)
            .order_by(TransactionTender.id)
            .limit(10_001)
        )
    ).all()
    initial_tenders = {
        tender_id: {"tender_type": tender_type, "reference": reference}
        for tender_id, tender_type, reference in tender_rows
    }
    initial_tender_type = tx.tender_type
    initial_reference = tx.payment_reference
    initial_customer_id = mask_customer_id(tx.customer_id)
    initial_customer_identification_type = tx.customer_identification_type
    initial_cashier_id = tx.cashier_id
    initial_salesperson_id = tx.salesperson_id
    initial_user_references = dict(tx.user_references or {})
    history_source = (
        await db.get(SourceRecord, tx.source_record_id) if tx.source_record_id else None
    )
    initial_paid_out_details = resolve_paid_out_details(tx, history_source)
    initial_customer_attributes = dict(tx.customer_attributes or {})
    applied_adjustments = sorted(
        (row for row in adjustments if row.status == "Approved"),
        key=lambda row: (row.reviewed_at or row.created_at, row.id),
        reverse=True,
    )
    for adjustment in applied_adjustments:
        values = adjustment.proposed_values if isinstance(adjustment.proposed_values, dict) else {}
        if (
            adjustment.adjustment_type == "Item"
            and values.get("line_no") is not None
            and values.get("before_item_code") is not None
        ):
            initial_items[str(values["line_no"])] = values["before_item_code"]
        if adjustment.adjustment_type == "Discount":
            operation = values.get("operation", "update")
            if operation == "add":
                initial_discounts.pop(values.get("created_discount_id") or adjustment.id, None)
            elif values.get("discount_id"):
                initial_discounts[values["discount_id"]] = {
                    field: values.get(f"before_{field}")
                    for field in ("kind", "code", "description", "line_no", "amount")
                }
        if adjustment.adjustment_type == "Tender" and values.get("before_tender_type") is not None:
            if values.get("tender_id"):
                initial_tenders.setdefault(values["tender_id"], {})["tender_type"] = values[
                    "before_tender_type"
                ]
            if values.get("updates_header") or not values.get("tender_id"):
                initial_tender_type = values["before_tender_type"]
        if adjustment.adjustment_type == "Reference" and "before_reference" in values:
            if values.get("tender_id"):
                initial_tenders.setdefault(values["tender_id"], {})["reference"] = values[
                    "before_reference"
                ]
            if values.get("updates_header") or not values.get("tender_id"):
                initial_reference = values["before_reference"]
        if adjustment.adjustment_type == "Customer":
            if "before_customer_id" in values:
                initial_customer_id = mask_customer_id(values.get("before_customer_id"))
            if "before_customer_identification_type" in values:
                initial_customer_identification_type = values.get(
                    "before_customer_identification_type"
                )
        if adjustment.adjustment_type == "Employee":
            if "before_cashier_id" in values:
                initial_cashier_id = values["before_cashier_id"]
            if "before_salesperson_id" in values:
                initial_salesperson_id = values["before_salesperson_id"]
        if adjustment.adjustment_type == "UserReference":
            reference_field = values.get("reference_field")
            if reference_field:
                if values.get("before_value"):
                    initial_user_references[reference_field] = values["before_value"]
                else:
                    initial_user_references.pop(reference_field, None)
        if adjustment.adjustment_type == "PaidOutDetail":
            field_name = values.get("field")
            if field_name:
                if values.get("before_value"):
                    initial_paid_out_details[field_name] = values["before_value"]
                else:
                    initial_paid_out_details.pop(field_name, None)
        if adjustment.adjustment_type == "CustomerAttribute":
            attribute_type = values.get("attribute_type")
            if attribute_type and "before_value" in values:
                if values["before_value"] is None:
                    initial_customer_attributes.pop(attribute_type, None)
                else:
                    initial_customer_attributes[attribute_type] = values["before_value"]
    initial_customer_attributes = redact_customer_attributes(initial_customer_attributes) or {}
    history = build_history(
        {
            "amount": tx.signed_amount,
            "tax": Decimal(recorded_tax),
            "tender": initial_tender_type,
            "reference": initial_reference,
            "customer_id": initial_customer_id,
            "customer_identification_type": initial_customer_identification_type,
            "cashier_id": initial_cashier_id,
            "salesperson_id": initial_salesperson_id,
            "user_references": redact_sensitive_fields(initial_user_references),
            "paid_out_details": redact_sensitive_fields(initial_paid_out_details),
            "customer_attributes": initial_customer_attributes,
            "items": initial_items,
            "discounts": initial_discounts,
            "tenders": initial_tenders,
        },
        adjustments[:MAX_TRANSACTION_ADJUSTMENTS],
    )
    history["truncated"] = len(adjustments) > MAX_TRANSACTION_ADJUSTMENTS
    history["currency"] = tx.currency
    if user["role"] not in {"finance", "it"}:
        for state_name in ("original", "current"):
            history[state_name]["customer_attributes"] = {}
        for change in history["changes"]:
            if change["adjustment_type"] == "CustomerAttribute":
                change["field"] = "Customer attribute"
                change["before"] = "••••" if change["before"] is not None else None
                change["after"] = "••••" if change["after"] is not None else None
    return redact_sensitive_fields(history)


@router.get("/{transaction_id}/item-lines")
async def transaction_item_lines(
    transaction_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it")),
):
    """List item lines for governed item and line-tax corrections, beyond the detail preview."""
    tx = (
        await db.execute(
            select(CanonicalTransaction).where(CanonicalTransaction.id == transaction_id)
        )
    ).scalar_one_or_none()
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")
    scope.require_store(user, tx.store_id)
    rows = (
        await db.execute(
            select(
                TransactionLine.line_no,
                TransactionLine.item_code,
                TransactionLine.description,
                TransactionLine.tax_code,
                TransactionLine.tax_rate,
                TransactionLine.tax_amount,
            )
            .where(TransactionLine.transaction_id == tx.id)
            .order_by(TransactionLine.line_no)
            .limit(10_001)
        )
    ).all()
    return {
        "items": [
            {
                "line_no": line_no,
                "item_code": item_code,
                "description": description,
                "tax_code": tax_code,
                "tax_rate": tax_rate,
                "tax_amount": tax_amount,
            }
            for line_no, item_code, description, tax_code, tax_rate, tax_amount in rows[:10_000]
        ],
        "truncated": len(rows) > 10_000,
    }


@router.get("/{transaction_id}/tender-lines")
async def transaction_tender_lines(
    transaction_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it")),
):
    """List tender details for corrections that need to target one payment in a split tender."""
    tx = (
        await db.execute(
            select(CanonicalTransaction).where(CanonicalTransaction.id == transaction_id)
        )
    ).scalar_one_or_none()
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")
    scope.require_store(user, tx.store_id)
    mask_character = await credit_card_number_mask(db)
    rows = (
        await db.execute(
            select(
                TransactionTender.id,
                TransactionTender.tender_type,
                TransactionTender.amount,
                TransactionTender.reference,
            )
            .where(TransactionTender.transaction_id == tx.id)
            .order_by(TransactionTender.id)
            .limit(10_001)
        )
    ).all()
    return {
        "items": [
            {
                "id": tender_id,
                "tender_type": tender_type,
                "amount": amount,
                "reference": mask_pan_value(reference, mask_character),
            }
            for tender_id, tender_type, amount, reference in rows[:10_000]
        ],
        "truncated": len(rows) > 10_000,
    }


@router.get("/{transaction_id}/tax-lines")
async def transaction_tax_lines(
    transaction_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it")),
):
    """List tax summary rows for a governed tax detail correction."""
    tx = (
        await db.execute(
            select(CanonicalTransaction).where(CanonicalTransaction.id == transaction_id)
        )
    ).scalar_one_or_none()
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")
    scope.require_store(user, tx.store_id)
    rows = (
        (
            await db.execute(
                select(TransactionTax)
                .where(TransactionTax.transaction_id == tx.id)
                .order_by(TransactionTax.tax_code, TransactionTax.id)
                .limit(10_001)
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [
            {
                "id": row.id,
                "tax_code": row.tax_code,
                "tax_name": row.tax_name,
                "rate": row.rate,
                "taxable_amount": row.taxable_amount,
                "tax_amount": row.tax_amount,
            }
            for row in rows[:10_000]
        ],
        "truncated": len(rows) > 10_000,
    }


@router.get("/{transaction_id}/discount-lines")
async def transaction_discount_lines(
    transaction_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it")),
):
    """List discount rows that can be targeted by a governed correction."""
    tx = (
        await db.execute(
            select(CanonicalTransaction).where(CanonicalTransaction.id == transaction_id)
        )
    ).scalar_one_or_none()
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")
    scope.require_store(user, tx.store_id)
    rows = (
        (
            await db.execute(
                select(TransactionDiscount)
                .where(TransactionDiscount.transaction_id == tx.id)
                .order_by(
                    TransactionDiscount.line_no.nulls_last(),
                    TransactionDiscount.kind,
                    TransactionDiscount.id,
                )
                .limit(10_001)
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [
            {
                "id": row.id,
                "kind": row.kind,
                "code": row.code,
                "description": row.description,
                "line_no": row.line_no,
                "amount": row.amount,
            }
            for row in rows[:10_000]
        ],
        "truncated": len(rows) > 10_000,
    }


@router.get("/{transaction_id}/detail/{section}.csv")
async def export_transaction_detail_section(
    transaction_id: str,
    section: Literal["items", "discounts", "taxes", "tenders"],
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("analyst", "finance", "it", "auditor")),
):
    """Stream every row in one transaction detail section, beyond the screen preview."""
    transaction = await db.get(CanonicalTransaction, transaction_id)
    if transaction is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    scope.require_store(user, transaction.store_id)

    section_model = {
        "items": TransactionLine,
        "discounts": TransactionDiscount,
        "taxes": TransactionTax,
        "tenders": TransactionTender,
    }[section]
    count = await db.scalar(
        select(func.count())
        .select_from(section_model)
        .where(section_model.transaction_id == transaction.id)
    )
    if count and count > MAX_TRANSACTION_DETAIL_EXPORT_ROWS:
        raise HTTPException(
            status_code=413,
            detail=(
                f"This section contains {count:,} rows; the export limit is "
                f"{MAX_TRANSACTION_DETAIL_EXPORT_ROWS:,}."
            ),
        )

    if section == "items":
        line_discounts = (
            select(
                TransactionDiscount.line_no.label("line_no"),
                func.sum(TransactionDiscount.amount).label("discount_amount"),
            )
            .where(
                TransactionDiscount.transaction_id == transaction.id,
            )
            .group_by(TransactionDiscount.line_no)
            .subquery()
        )
        query = (
            select(
                TransactionLine.line_no,
                TransactionLine.item_code,
                TransactionLine.description,
                TransactionLine.quantity,
                TransactionLine.unit_price,
                TransactionLine.original_unit_price,
                TransactionLine.price_override_reason,
                TransactionLine.gross_amount,
                TransactionLine.tax_code,
                TransactionLine.tax_rate,
                TransactionLine.tax_amount,
                func.coalesce(line_discounts.c.discount_amount, 0),
                TransactionLine.return_reason,
                TransactionLine.source_attributes,
            )
            .outerjoin(line_discounts, line_discounts.c.line_no == TransactionLine.line_no)
            .where(TransactionLine.transaction_id == transaction.id)
            .order_by(TransactionLine.line_no, TransactionLine.id)
        )
        headers = (
            "Line",
            "Item code",
            "Description",
            "Quantity",
            "Unit price",
            "Original unit price",
            "Price override reason",
            "Gross amount",
            "Tax code",
            "Tax rate",
            "Tax amount",
            "Discount amount",
            "Return reason",
            "POS item attributes",
        )
    elif section == "discounts":
        query = (
            select(
                TransactionDiscount.kind,
                TransactionDiscount.code,
                TransactionDiscount.description,
                TransactionDiscount.line_no,
                TransactionDiscount.amount,
            )
            .where(TransactionDiscount.transaction_id == transaction.id)
            .order_by(
                TransactionDiscount.line_no.nulls_last(),
                TransactionDiscount.kind,
                TransactionDiscount.id,
            )
        )
        headers = ("Kind", "Code", "Description", "Line", "Amount")
    elif section == "taxes":
        query = (
            select(
                TransactionTax.tax_code,
                TransactionTax.tax_name,
                TransactionTax.rate,
                TransactionTax.taxable_amount,
                TransactionTax.tax_amount,
            )
            .where(TransactionTax.transaction_id == transaction.id)
            .order_by(TransactionTax.tax_code, TransactionTax.id)
        )
        headers = ("Tax code", "Tax name", "Rate", "Taxable amount", "Tax amount")
    else:
        query = (
            select(
                TransactionTender.tender_type,
                TransactionTender.amount,
                TransactionTender.reference,
                TransactionTender.authorization,
                TransactionTender.source_attributes,
            )
            .where(TransactionTender.transaction_id == transaction.id)
            .order_by(TransactionTender.tender_type, TransactionTender.id)
        )
        headers = ("Tender type", "Amount", "Reference", "Authorization", "POS tender attributes")
    headers = ("Transaction ID", "Store", "Business date", "Currency", *headers)

    mask_character = await credit_card_number_mask(db) if section == "tenders" else "•"

    async def rows():
        yield csv_row(headers)
        result = await db.stream(query.execution_options(yield_per=1000))
        try:
            async for record in result:
                values = list(record)
                if section == "items":
                    values[-1] = json.dumps(
                        redact_sensitive_fields(values[-1] or {}),
                        sort_keys=True,
                        default=str,
                    )
                elif section == "tenders":
                    values[2] = mask_pan_value(values[2], mask_character)
                    values[3] = mask_pan_value(values[3], mask_character)
                    values[4] = json.dumps(
                        redact_sensitive_fields(values[4] or {}),
                        sort_keys=True,
                        default=str,
                    )
                yield csv_row(
                    (
                        transaction.id,
                        transaction.store_id,
                        transaction.business_date,
                        transaction.currency,
                        *values,
                    )
                )
        finally:
            await result.close()

    return StreamingResponse(
        rows(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="transaction-{transaction.id}-{section}.csv"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/{transaction_id}/related-cases")
async def transaction_related_cases(
    transaction_id: str,
    offset: int = Query(ge=0, le=100000, default=0),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("analyst", "finance", "it", "auditor")),
):
    """Return a bounded page of cases raised from a transaction or its store day."""
    tx = await db.get(CanonicalTransaction, transaction_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")
    scope.require_store(user, tx.store_id)
    causal_case_ids = select(AuditEvent.case_id).where(
        AuditEvent.event_type == "CASE_CREATED",
        case_creation_mentions_transaction(db, tx.id),
    )
    causal = Case.id.in_(causal_case_ids)
    query = select(Case, causal.label("is_causal")).where(
        or_(
            causal,
            (Case.store_id == tx.store_id) & (Case.business_date == tx.business_date),
        )
    )
    query = scope.restrict(query, user, Case.store_id).order_by(
        sql_case((causal, 0), else_=1), Case.case_number, Case.id
    )
    cases = (await db.execute(query.offset(offset).limit(MAX_RELATED_CASES + 1))).all()
    return {
        "items": [
            {
                "id": case.id,
                "case_number": case.case_number,
                "case_type": case.case_type,
                "status": case.status,
                "link": (
                    "raised from this transaction"
                    if is_causal
                    else "same store and day"
                ),
            }
            for case, is_causal in cases[:MAX_RELATED_CASES]
        ],
        "has_more": len(cases) > MAX_RELATED_CASES,
    }


@router.get("/{transaction_id}")
async def transaction_detail(
    transaction_id: str,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("analyst", "finance", "it", "auditor")),
):
    tx = (
        await db.execute(
            select(CanonicalTransaction).where(CanonicalTransaction.id == transaction_id)
        )
    ).scalar_one_or_none()
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")
    scope.require_store(_, tx.store_id)
    source = (
        (
            await db.execute(select(SourceRecord).where(SourceRecord.id == tx.source_record_id))
        ).scalar_one_or_none()
        if tx.source_record_id
        else None
    )
    paid_out_values = resolve_paid_out_details(tx, source)

    # Cases that were raised from this transaction (the case-creation event lists the transactions behind it).
    candidate_case_ids = select(AuditEvent.case_id).where(
        AuditEvent.event_type == "CASE_CREATED", case_creation_mentions_transaction(db, tx.id)
    )
    causal_query = scope.restrict(
        select(Case.id).where(Case.id.in_(candidate_case_ids)), _, Case.store_id
    )
    causal_query = causal_query.order_by(Case.case_number, Case.id).limit(MAX_RELATED_CASES + 1)
    causal_ids = set((await db.execute(causal_query)).scalars())
    related_case_query = (
        select(Case)
        .where(
            or_(
                (Case.id.in_(causal_ids)) if causal_ids else False,
                (Case.store_id == tx.store_id) & (Case.business_date == tx.business_date),
            )
        )
        .order_by(
            sql_case((Case.id.in_(causal_ids), 0), else_=1),
            Case.case_number,
            Case.id,
        )
    )
    related_case_query = scope.restrict(related_case_query, _, Case.store_id).limit(
        MAX_RELATED_CASES + 1
    )
    related_cases = (await db.execute(related_case_query)).scalars().all()
    cases_truncated = len(related_cases) > MAX_RELATED_CASES
    cases = [
        {
            "id": c.id,
            "case_number": c.case_number,
            "case_type": c.case_type,
            "status": c.status,
            "link": "raised from this transaction" if c.id in causal_ids else "same store and day",
        }
        for c in related_cases[:MAX_RELATED_CASES]
    ]
    errors = []
    errors_truncated = False
    if causal_ids:
        error_rows = (
            await db.execute(
                scope.restrict(
                    select(DBException, Case)
                    .join(Case, Case.id == DBException.case_id)
                    .where(DBException.case_id.in_(candidate_case_ids))
                    .order_by(DBException.created_at.desc(), DBException.id)
                    .limit(MAX_TRANSACTION_ERRORS + 1),
                    _,
                    Case.store_id,
                )
            )
        ).all()
        errors_truncated = len(error_rows) > MAX_TRANSACTION_ERRORS
        errors = [
            {
                "id": exception.id,
                "code": exception.exception_type,
                "family": exception.exception_family,
                "detection_origin": exception.detection_origin,
                "severity": exception.severity,
                "confidence": exception.confidence,
                "status": exception.status,
                "close_impact": exception.close_impact,
                "amount": str(exception.exception_amount),
                "case_id": case.id,
                "case_number": case.case_number,
                "case_status": case.status,
            }
            for exception, case in error_rows[:MAX_TRANSACTION_ERRORS]
        ]

    refs = [r for r in (tx.payment_reference, tx.settlement_reference) if r]
    linked = []
    if refs:
        query = scope.restrict(
            select(CanonicalTransaction, SourceRecord).outerjoin(
                SourceRecord, CanonicalTransaction.source_record_id == SourceRecord.id
            ),
            _,
            CanonicalTransaction.store_id,
        )
        rows = (
            await db.execute(
                query.where(
                    CanonicalTransaction.id != tx.id,
                    or_(
                        CanonicalTransaction.payment_reference.in_(refs),
                        CanonicalTransaction.settlement_reference.in_(refs),
                    ),
                )
                .order_by(CanonicalTransaction.event_timestamp)
                .limit(20)
            )
        ).all()
        linked = [row_view(t, s) for t, s in rows]
    adjustment_rows = (
        (
            await db.execute(
                select(TransactionAdjustment)
                .where(TransactionAdjustment.transaction_id == tx.id)
                .order_by(TransactionAdjustment.created_at.desc(), TransactionAdjustment.id)
                .limit(MAX_TRANSACTION_ADJUSTMENTS + 1)
            )
        )
        .scalars()
        .all()
    )
    adjustments_truncated = len(adjustment_rows) > MAX_TRANSACTION_ADJUSTMENTS
    adjustments = adjustment_rows[:MAX_TRANSACTION_ADJUSTMENTS]
    out = row_view(tx, source)
    out.update(
        {
            "transaction_subtype": tx.transaction_subtype,
            "settlement_reference": tx.settlement_reference,
            "settlement_date": tx.settlement_date,
            "processing_timestamp": tx.processing_timestamp,
            "channel_id": tx.channel_id,
            "quantity": tx.quantity,
            "source_lineage": tx.source_lineage,
            "disposition": tx.disposition,
            "paid_out_details": paid_out_values,
        }
    )
    out["source"] = (
        None
        if not source
        else {
            "id": source.id,
            "source_system": source.source_system,
            "source_record_id": source.source_record_id,
            "source_version": source.source_version,
            "delivery_id": source.delivery_id,
            "status": source.status,
            "received_at": source.received_at,
            "source_event_time": source.source_event_time,
            "payload_hash": source.payload_hash,
            "quarantine_reason": source.quarantine_reason,
            "payload": decode_payload(source.payload),
        }
    )
    line_count, gross, line_tax = (
        await db.execute(
            select(
                func.count(),
                func.coalesce(func.sum(TransactionLine.gross_amount), 0),
                func.coalesce(func.sum(TransactionLine.tax_amount), 0),
            ).where(TransactionLine.transaction_id == tx.id)
        )
    ).one()
    discount_count, discount_total = (
        await db.execute(
            select(
                func.count(),
                func.coalesce(func.sum(TransactionDiscount.amount), 0),
            ).where(TransactionDiscount.transaction_id == tx.id)
        )
    ).one()
    tax_count, tax_total = (
        await db.execute(
            select(
                func.count(),
                func.coalesce(func.sum(TransactionTax.tax_amount), 0),
            ).where(TransactionTax.transaction_id == tx.id)
        )
    ).one()
    tender_count, tender_total = (
        await db.execute(
            select(
                func.count(),
                func.coalesce(func.sum(TransactionTender.amount), 0),
            ).where(TransactionTender.transaction_id == tx.id)
        )
    ).one()

    lines = (
        (
            await db.execute(
                select(TransactionLine)
                .where(
                    TransactionLine.transaction_id == tx.id,
                )
                .order_by(TransactionLine.line_no, TransactionLine.id)
                .limit(MAX_TRANSACTION_DETAIL_ROWS + 1)
            )
        )
        .scalars()
        .all()
    )
    discounts = (
        (
            await db.execute(
                select(TransactionDiscount)
                .where(
                    TransactionDiscount.transaction_id == tx.id,
                )
                .order_by(
                    TransactionDiscount.line_no.nulls_last(),
                    TransactionDiscount.kind,
                    TransactionDiscount.id,
                )
                .limit(MAX_TRANSACTION_DETAIL_ROWS + 1)
            )
        )
        .scalars()
        .all()
    )
    taxes = (
        (
            await db.execute(
                select(TransactionTax)
                .where(
                    TransactionTax.transaction_id == tx.id,
                )
                .order_by(TransactionTax.tax_code, TransactionTax.id)
                .limit(MAX_TRANSACTION_DETAIL_ROWS + 1)
            )
        )
        .scalars()
        .all()
    )
    tenders = (
        (
            await db.execute(
                select(TransactionTender)
                .where(
                    TransactionTender.transaction_id == tx.id,
                )
                .order_by(TransactionTender.tender_type, TransactionTender.id)
                .limit(MAX_TRANSACTION_DETAIL_ROWS + 1)
            )
        )
        .scalars()
        .all()
    )
    lines_truncated = line_count > MAX_TRANSACTION_DETAIL_ROWS
    discounts_truncated = discount_count > MAX_TRANSACTION_DETAIL_ROWS
    taxes_truncated = tax_count > MAX_TRANSACTION_DETAIL_ROWS
    tenders_truncated = tender_count > MAX_TRANSACTION_DETAIL_ROWS
    lines, discounts, taxes, tenders = (
        rows[:MAX_TRANSACTION_DETAIL_ROWS] for rows in (lines, discounts, taxes, tenders)
    )
    line_discount_totals = (
        dict(
            (
                await db.execute(
                    select(
                        TransactionDiscount.line_no,
                        func.sum(TransactionDiscount.amount),
                    )
                    .where(
                        TransactionDiscount.transaction_id == tx.id,
                        TransactionDiscount.line_no.in_([line.line_no for line in lines]),
                    )
                    .group_by(TransactionDiscount.line_no)
                )
            ).all()
        )
        if lines
        else {}
    )
    summary = detail_math.check_totals(
        Decimal(gross),
        Decimal(discount_total),
        Decimal(line_tax if line_count else tax_total),
        Decimal(tender_total),
        tx.signed_amount,
        has_lines=line_count > 0,
        has_tenders=tender_count > 0,
    )
    out["detail"] = {
        "status": summary["status"],
        "line_count": line_count,
        "lines_truncated": lines_truncated,
        "lines": [
            {
                "line_no": line.line_no,
                "item_code": line.item_code,
                "description": line.description,
                "quantity": line.quantity,
                "unit_price": line.unit_price,
                "original_unit_price": line.original_unit_price,
                "price_override_reason": line.price_override_reason,
                "source_attributes": line.source_attributes,
                "gross_amount": line.gross_amount,
                "tax_code": line.tax_code,
                "tax_rate": line.tax_rate,
                "tax_amount": line.tax_amount,
                "discount_amount": line_discount_totals.get(line.line_no, Decimal("0")),
                "return_reason": line.return_reason,
            }
            for line in lines
        ],
        "discount_count": discount_count,
        "discounts_truncated": discounts_truncated,
        "discounts": [
            {
                "id": d.id,
                "kind": d.kind,
                "code": d.code,
                "description": d.description,
                "line_no": d.line_no,
                "amount": d.amount,
            }
            for d in discounts
        ],
        "tax_count": tax_count,
        "taxes_truncated": taxes_truncated,
        "taxes": [
            {
                "id": t.id,
                "tax_code": t.tax_code,
                "tax_name": t.tax_name,
                "rate": t.rate,
                "taxable_amount": t.taxable_amount,
                "tax_amount": t.tax_amount,
            }
            for t in taxes
        ],
        "tender_count": tender_count,
        "tenders_truncated": tenders_truncated,
        "tenders": [
            {
                "tender_type": t.tender_type,
                "amount": t.amount,
                "reference": t.reference,
                "authorization": t.authorization,
                "source_attributes": t.source_attributes,
            }
            for t in tenders
        ],
        "totals": None
        if summary["status"] == "No detail"
        else {
            k: summary[k]
            for k in ("gross", "discounts", "tax", "net", "expected_total", "tendered")
            if k in summary
        },
        "total_variance": summary.get("total_variance"),
        "tender_variance": summary.get("tender_variance"),
    }
    out["returns"] = (
        await return_context(db, tx) if (tx.transaction_type in ("Return", "Sale")) else None
    )
    out["cases"], out["cases_truncated"], out["linked_transactions"] = (
        cases,
        cases_truncated,
        linked,
    )
    out["errors"], out["errors_truncated"] = errors, errors_truncated
    out["adjustments"] = [
        {
            "id": a.id,
            "adjustment_type": a.adjustment_type,
            "status": a.status,
            "rationale": a.rationale,
            "proposed_by": a.proposed_by,
            "created_at": a.created_at,
        }
        for a in adjustments
    ]
    out["adjustments_truncated"] = adjustments_truncated
    return redact_sensitive_fields(out)
