"""Detailed transaction, return lineage, and related-record endpoints."""

import json
from collections import defaultdict
from decimal import Decimal

from database import get_db
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case as sql_case
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from apps.audit.models import AuditEvent
from apps.cases.lineage import case_creation_mentions_transaction
from apps.cases.models import Case
from apps.corrections.models import TransactionAdjustment
from apps.ingest.models import SourceRecord
from apps.security import scope
from apps.security.authentication import require_role
from apps.transactions import detail as detail_math
from apps.transactions.models import (
    CanonicalTransaction,
    TransactionDiscount,
    TransactionLine,
    TransactionTax,
    TransactionTender,
)
from apps.transactions.views import row_view

router = APIRouter(tags=["Transactions"])

MAX_PAYLOAD_CHARS = 8000
MAX_RELATED_CASES = 200
MAX_RELATED_RETURNS = 200
MAX_TRANSACTION_DETAIL_ROWS = 500
MAX_RETURN_ITEM_ROWS = 100
MAX_TRANSACTION_ADJUSTMENTS = 200


def decode_payload(raw: bytes | None) -> dict:
    if not raw:
        return {"text": None, "truncated": False}
    truncated = len(raw) > MAX_PAYLOAD_CHARS
    text = raw[:MAX_PAYLOAD_CHARS].decode("utf-8", errors="replace")
    if not truncated:
        try:
            text = json.dumps(json.loads(text), indent=2, default=str)
        except ValueError:
            pass
    return {
        "text": text[:MAX_PAYLOAD_CHARS],
        "truncated": truncated or len(text) > MAX_PAYLOAD_CHARS,
    }


def fmt_qty(q) -> str:
    return format(Decimal(q).normalize(), "f")


async def return_context(db, tx: CanonicalTransaction) -> dict:
    """For a return: the sale it reverses and how much of each item came back. For a sale: the returns made against it."""
    if tx.transaction_type == "Return":
        original = (
            (
                await db.execute(
                    select(CanonicalTransaction, SourceRecord)
                    .outerjoin(
                        SourceRecord, CanonicalTransaction.source_record_id == SourceRecord.id
                    )
                    .where(
                        CanonicalTransaction.id == tx.original_transaction_id,
                        CanonicalTransaction.store_id == tx.store_id,
                    )
                )
            ).first()
            if tx.original_transaction_id
            else None
        )
        current_items = (
            select(
                TransactionLine.item_code.label("item_code"),
                func.sum(func.abs(TransactionLine.quantity)).label("quantity"),
                func.max(TransactionLine.description).label("description"),
                func.max(TransactionLine.return_reason).label("return_reason"),
            )
            .where(TransactionLine.transaction_id == tx.id)
            .group_by(TransactionLine.item_code)
            .subquery()
        )
        sold_items = None
        if original:
            sold_items = (
                select(
                    TransactionLine.item_code.label("item_code"),
                    func.sum(func.abs(TransactionLine.quantity)).label("quantity"),
                )
                .where(TransactionLine.transaction_id == original[0].id)
                .group_by(TransactionLine.item_code)
                .subquery()
            )
            prior_return_totals = (
                select(
                    TransactionLine.item_code.label("item_code"),
                    func.sum(func.abs(TransactionLine.quantity)).label("quantity"),
                )
                .join(
                    CanonicalTransaction, CanonicalTransaction.id == TransactionLine.transaction_id
                )
                .where(
                    CanonicalTransaction.original_transaction_id == original[0].id,
                    CanonicalTransaction.id != tx.id,
                    CanonicalTransaction.store_id == tx.store_id,
                )
                .group_by(TransactionLine.item_code)
                .subquery()
            )
            current_query = current_items.outerjoin(
                sold_items, sold_items.c.item_code == current_items.c.item_code
            ).outerjoin(
                prior_return_totals, prior_return_totals.c.item_code == current_items.c.item_code
            )
            over_returned_query = (
                select(func.count())
                .select_from(current_query)
                .where(
                    or_(
                        sold_items.c.item_code.is_(None),
                        func.coalesce(prior_return_totals.c.quantity, 0) + current_items.c.quantity
                        > sold_items.c.quantity,
                    )
                )
            )
            over_returned = bool((await db.execute(over_returned_query)).scalar_one())
        else:
            prior_return_totals = None
            current_query = current_items
            over_returned = False

        current_query = select(current_items)
        if sold_items is not None:
            current_query = select(
                current_items.c.item_code,
                current_items.c.quantity,
                current_items.c.description,
                current_items.c.return_reason,
                sold_items.c.quantity.label("sold_quantity"),
                prior_return_totals.c.quantity.label("prior_quantity"),
            ).select_from(
                current_items.outerjoin(
                    sold_items, sold_items.c.item_code == current_items.c.item_code
                ).outerjoin(
                    prior_return_totals,
                    prior_return_totals.c.item_code == current_items.c.item_code,
                )
            )
        current_rows = (
            await db.execute(
                current_query.order_by(current_items.c.item_code).limit(MAX_RETURN_ITEM_ROWS + 1)
            )
        ).all()
        items_truncated = len(current_rows) > MAX_RETURN_ITEM_ROWS
        items = []
        for row in current_rows[:MAX_RETURN_ITEM_ROWS]:
            sold_q = row.sold_quantity if sold_items is not None else None
            before = row.prior_quantity or Decimal("0") if sold_items is not None else Decimal("0")
            now = row.quantity
            items.append(
                {
                    "item_code": row.item_code,
                    "description": row.description,
                    "returned_qty": fmt_qty(now),
                    "sold_qty": None if sold_q is None else fmt_qty(sold_q),
                    "already_returned_qty": fmt_qty(before),
                    "reason": row.return_reason or tx.return_reason,
                    "over_returned": original is not None
                    and (sold_q is None or before + now > sold_q),
                }
            )
        return {
            "is_return": True,
            "original_reference": tx.original_reference,
            "reason": tx.return_reason,
            "original": None
            if not original
            else {
                "id": original[0].id,
                "business_date": original[0].business_date,
                "amount": original[0].signed_amount,
                "source_record_id": original[1].source_record_id if original[1] else None,
            },
            "items": items,
            "items_truncated": items_truncated,
            "over_returned": over_returned,
        }
    rets = (
        (
            await db.execute(
                select(CanonicalTransaction)
                .where(
                    CanonicalTransaction.original_transaction_id == tx.id,
                    CanonicalTransaction.store_id == tx.store_id,
                )
                .order_by(CanonicalTransaction.event_timestamp, CanonicalTransaction.id)
                .limit(MAX_RELATED_RETURNS + 1)
            )
        )
        .scalars()
        .all()
    )
    returns_truncated = len(rets) > MAX_RELATED_RETURNS
    rets = rets[:MAX_RELATED_RETURNS]
    return_ids = [item.id for item in rets]
    return_lines: dict[str, list[TransactionLine]] = defaultdict(list)
    return_lines_truncated: set[str] = set()
    if return_ids:
        ranked_lines = (
            select(
                TransactionLine,
                func.row_number()
                .over(
                    partition_by=TransactionLine.transaction_id,
                    order_by=(TransactionLine.line_no, TransactionLine.id),
                )
                .label("row_number"),
            )
            .where(TransactionLine.transaction_id.in_(return_ids))
            .subquery()
        )
        ranked_line = aliased(TransactionLine, ranked_lines)
        line_rows = (
            await db.execute(
                select(ranked_line, ranked_lines.c.row_number)
                .where(
                    ranked_lines.c.row_number <= MAX_RETURN_ITEM_ROWS + 1,
                )
                .order_by(ranked_line.transaction_id, ranked_line.line_no, ranked_line.id)
            )
        ).all()
        for line, row_number in line_rows:
            if row_number > MAX_RETURN_ITEM_ROWS:
                return_lines_truncated.add(line.transaction_id)
            else:
                return_lines[line.transaction_id].append(line)
    out = []
    for r in rets:
        rl = return_lines[r.id]
        out.append(
            {
                "id": r.id,
                "business_date": r.business_date,
                "amount": r.signed_amount,
                "reason": r.return_reason,
                "status": r.reconciliation_status,
                "items": [
                    {"item_code": line.item_code, "quantity": fmt_qty(abs(line.quantity))}
                    for line in rl
                ],
                "items_truncated": r.id in return_lines_truncated,
            }
        )
    return {"is_return": False, "returns": out, "returns_truncated": returns_truncated}


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
            sql_case((Case.id.in_(causal_ids), 0), else_=1) if causal_ids else Case.case_number,
            Case.case_number,
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
    return out
