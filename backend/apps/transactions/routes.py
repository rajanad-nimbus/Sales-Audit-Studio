"""Browse every canonical transaction, with the raw source record and the cases it relates to."""
import json
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_role
from database import get_db
import transaction_detail as detail_math
from models import (AuditEvent, CanonicalTransaction, Case, SourceRecord, TransactionAdjustment, TransactionDiscount, TransactionLine,
                    TransactionTax, TransactionTender)

router = APIRouter(prefix="/api/transactions", tags=["Transactions"])
MAX_PAYLOAD_CHARS = 8000
SORTS = {
    "business_date": CanonicalTransaction.business_date, "event_timestamp": CanonicalTransaction.event_timestamp,
    "amount": CanonicalTransaction.signed_amount, "store": CanonicalTransaction.store_id,
    "type": CanonicalTransaction.transaction_type, "status": CanonicalTransaction.reconciliation_status,
    "tender": CanonicalTransaction.tender_type, "source": SourceRecord.source_system,
}


def row_view(tx: CanonicalTransaction, source: SourceRecord | None, items: int | None = None) -> dict:
    return {"item_count": items, "detail_status": tx.detail_status, "original_reference": tx.original_reference, "id": tx.id, "business_date": tx.business_date, "event_timestamp": tx.event_timestamp, "store_id": tx.store_id,
            "register_id": tx.register_id, "source_system": source.source_system if source else "Unknown",
            "source_record_id": source.source_record_id if source else None, "transaction_type": tx.transaction_type,
            "tender_type": tx.tender_type, "payment_reference": tx.payment_reference, "amount": tx.signed_amount,
            "currency": tx.currency, "reconciliation_status": tx.reconciliation_status}


def conditions(store_id=None, source_system=None, transaction_type=None, tender_type=None, status=None,
               date_from=None, date_to=None, q=None, skip_status=False, detail_status=None) -> list:
    c = []
    if store_id: c.append(CanonicalTransaction.store_id == store_id)
    if source_system: c.append(SourceRecord.source_system == source_system)
    if transaction_type: c.append(CanonicalTransaction.transaction_type == transaction_type)
    if tender_type: c.append(CanonicalTransaction.tender_type == tender_type)
    if status and not skip_status: c.append(CanonicalTransaction.reconciliation_status == status)
    if detail_status: c.append(CanonicalTransaction.detail_status == detail_status)
    if date_from: c.append(CanonicalTransaction.business_date >= date_from)
    if date_to: c.append(CanonicalTransaction.business_date <= date_to)
    if q and q.strip():
        like = f"%{q.strip()}%"
        c.append(or_(CanonicalTransaction.id.ilike(like), CanonicalTransaction.payment_reference.ilike(like),
                     CanonicalTransaction.settlement_reference.ilike(like), CanonicalTransaction.register_id.ilike(like),
                     SourceRecord.source_record_id.ilike(like)))
    return c


@router.get("")
async def list_transactions(store_id: str | None = None, source_system: str | None = None, transaction_type: str | None = None,
                            tender_type: str | None = None, status: str | None = None, date_from: str | None = None,
                            date_to: str | None = None, q: str | None = None, detail_status: str | None = None, sort: str = "business_date", dir: str = "desc",
                            page: int = 1, page_size: int = 50, db: AsyncSession = Depends(get_db),
                            _: dict = Depends(require_role("finance", "it"))):
    page_size, page = max(1, min(page_size, 200)), max(1, page)
    base = select(CanonicalTransaction, SourceRecord).outerjoin(SourceRecord, CanonicalTransaction.source_record_id == SourceRecord.id)
    where = conditions(store_id, source_system, transaction_type, tender_type, status, date_from, date_to, q, detail_status=detail_status)
    total = (await db.execute(select(func.count()).select_from(CanonicalTransaction).outerjoin(
        SourceRecord, CanonicalTransaction.source_record_id == SourceRecord.id).where(*where))).scalar() or 0
    col = SORTS.get(sort, CanonicalTransaction.business_date)
    order = col.desc().nulls_last() if dir == "desc" else col.asc().nulls_last()
    rows = (await db.execute(base.where(*where).order_by(order, CanonicalTransaction.event_timestamp.desc(), CanonicalTransaction.id)
                             .offset((page - 1) * page_size).limit(page_size))).all()
    # The status tiles show the spread across the other filters, so choosing one status does not hide the rest.
    spread_where = conditions(store_id, source_system, transaction_type, tender_type, status, date_from, date_to, q, skip_status=True, detail_status=detail_status)
    by_status = dict((await db.execute(select(CanonicalTransaction.reconciliation_status, func.count()).select_from(CanonicalTransaction)
                                       .outerjoin(SourceRecord, CanonicalTransaction.source_record_id == SourceRecord.id)
                                       .where(*spread_where).group_by(CanonicalTransaction.reconciliation_status))).all())
    net = (await db.execute(select(func.coalesce(func.sum(CanonicalTransaction.signed_amount), 0)).select_from(CanonicalTransaction)
                            .outerjoin(SourceRecord, CanonicalTransaction.source_record_id == SourceRecord.id).where(*where))).scalar()
    counts = dict((await db.execute(select(TransactionLine.transaction_id, func.count()).where(
        TransactionLine.transaction_id.in_([t.id for t, _ in rows])).group_by(TransactionLine.transaction_id))).all()) if rows else {}
    return {"items": [row_view(t, s, counts.get(t.id, 0)) for t, s in rows], "total": total, "page": page, "page_size": page_size,
            "pages": max(1, -(-total // page_size)), "by_status": by_status, "net_amount": str(net or Decimal("0"))}


@router.get("/facets")
async def facets(db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("finance", "it"))):
    async def distinct(col, joined=False):
        stmt = select(col).where(col.is_not(None)).distinct().order_by(col)
        return (await db.execute(stmt)).scalars().all()
    lo, hi = (await db.execute(select(func.min(CanonicalTransaction.business_date), func.max(CanonicalTransaction.business_date)))).one()
    return {"stores": await distinct(CanonicalTransaction.store_id), "sources": await distinct(SourceRecord.source_system),
            "types": await distinct(CanonicalTransaction.transaction_type), "tenders": await distinct(CanonicalTransaction.tender_type),
            "statuses": await distinct(CanonicalTransaction.reconciliation_status), "detail_statuses": await distinct(CanonicalTransaction.detail_status),
            "date_min": lo, "date_max": hi}


def decode_payload(raw: bytes | None) -> dict:
    if not raw:
        return {"text": None, "json": None, "truncated": False}
    text = raw.decode("utf-8", errors="replace")
    try:
        parsed = json.loads(text)
        text = json.dumps(parsed, indent=2, default=str)
    except ValueError:
        parsed = None
    return {"text": text[:MAX_PAYLOAD_CHARS], "truncated": len(text) > MAX_PAYLOAD_CHARS}


def fmt_qty(q) -> str:
    return format(Decimal(q).normalize(), "f")


def qty_by_item(lines) -> dict:
    out: dict = {}
    for ln in lines:
        out[ln.item_code] = out.get(ln.item_code, Decimal("0")) + abs(ln.quantity)
    return out


async def return_context(db, tx: CanonicalTransaction, lines: list) -> dict:
    """For a return: the sale it reverses and how much of each item came back. For a sale: the returns made against it."""
    if tx.transaction_type == "Return":
        original = (await db.execute(select(CanonicalTransaction, SourceRecord).outerjoin(SourceRecord, CanonicalTransaction.source_record_id == SourceRecord.id)
                                     .where(CanonicalTransaction.id == tx.original_transaction_id))).first() if tx.original_transaction_id else None
        sold, others = {}, {}
        if original:
            sold = qty_by_item((await db.execute(select(TransactionLine).where(TransactionLine.transaction_id == original[0].id))).scalars().all())
            other_returns = (await db.execute(select(TransactionLine).join(CanonicalTransaction, CanonicalTransaction.id == TransactionLine.transaction_id)
                                              .where(CanonicalTransaction.original_transaction_id == original[0].id, CanonicalTransaction.id != tx.id))).scalars().all()
            others = qty_by_item(other_returns)
        this = qty_by_item(lines)
        items = []
        for ln in lines:
            code = ln.item_code
            sold_q, before, now = sold.get(code), others.get(code, Decimal("0")), this[code]
            items.append({"item_code": code, "description": ln.description, "returned_qty": fmt_qty(abs(ln.quantity)), "sold_qty": None if sold_q is None else fmt_qty(sold_q),
                          "already_returned_qty": fmt_qty(before), "reason": ln.return_reason or tx.return_reason,
                          "over_returned": original is not None and (sold_q is None or before + now > sold_q)})
        return {"is_return": True, "original_reference": tx.original_reference, "reason": tx.return_reason,
                "original": None if not original else {"id": original[0].id, "business_date": original[0].business_date, "amount": original[0].signed_amount,
                                                       "source_record_id": original[1].source_record_id if original[1] else None},
                "items": items, "over_returned": any(i["over_returned"] for i in items)}
    rets = (await db.execute(select(CanonicalTransaction).where(CanonicalTransaction.original_transaction_id == tx.id)
                             .order_by(CanonicalTransaction.event_timestamp))).scalars().all()
    out = []
    for r in rets:
        rl = (await db.execute(select(TransactionLine).where(TransactionLine.transaction_id == r.id))).scalars().all()
        out.append({"id": r.id, "business_date": r.business_date, "amount": r.signed_amount, "reason": r.return_reason, "status": r.reconciliation_status,
                    "items": [{"item_code": l.item_code, "quantity": fmt_qty(abs(l.quantity))} for l in rl]})
    return {"is_return": False, "returns": out}


@router.get("/{transaction_id}")
async def transaction_detail(transaction_id: str, db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("finance", "it"))):
    tx = (await db.execute(select(CanonicalTransaction).where(CanonicalTransaction.id == transaction_id))).scalar_one_or_none()
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")
    source = (await db.execute(select(SourceRecord).where(SourceRecord.id == tx.source_record_id))).scalar_one_or_none() if tx.source_record_id else None

    # Cases that were raised from this transaction (the case-creation event lists the transactions behind it).
    created = (await db.execute(select(AuditEvent.case_id).where(
        AuditEvent.event_type == "CASE_CREATED", cast(AuditEvent.after_state, String).like(f"%{tx.id}%")))).scalars().all()
    causal_ids = {c for c in created if c}
    same_day = (await db.execute(select(Case).where(Case.store_id == tx.store_id, Case.business_date == tx.business_date))).scalars().all()
    cases = [{"id": c.id, "case_number": c.case_number, "case_type": c.case_type, "status": c.status,
              "link": "raised from this transaction" if c.id in causal_ids else "same store and day"} for c in same_day]
    seen = {c["id"] for c in cases}
    for cid in causal_ids - seen:   # raised from this transaction but for another store day
        c = (await db.execute(select(Case).where(Case.id == cid))).scalar_one_or_none()
        if c:
            cases.append({"id": c.id, "case_number": c.case_number, "case_type": c.case_type, "status": c.status, "link": "raised from this transaction"})
    cases.sort(key=lambda c: (c["link"] != "raised from this transaction", c["case_number"]))

    refs = [r for r in (tx.payment_reference, tx.settlement_reference) if r]
    linked = []
    if refs:
        rows = (await db.execute(select(CanonicalTransaction, SourceRecord).outerjoin(SourceRecord, CanonicalTransaction.source_record_id == SourceRecord.id)
                                 .where(CanonicalTransaction.id != tx.id,
                                        or_(CanonicalTransaction.payment_reference.in_(refs), CanonicalTransaction.settlement_reference.in_(refs)))
                                 .order_by(CanonicalTransaction.event_timestamp).limit(20))).all()
        linked = [row_view(t, s) for t, s in rows]
    adjustments = (await db.execute(select(TransactionAdjustment).where(TransactionAdjustment.transaction_id == tx.id)
                                    .order_by(TransactionAdjustment.created_at.desc()))).scalars().all()
    out = row_view(tx, source)
    out.update({"transaction_subtype": tx.transaction_subtype, "settlement_reference": tx.settlement_reference, "settlement_date": tx.settlement_date,
                "processing_timestamp": tx.processing_timestamp, "channel_id": tx.channel_id, "quantity": tx.quantity,
                "source_lineage": tx.source_lineage, "disposition": tx.disposition})
    out["source"] = None if not source else {
        "id": source.id, "source_system": source.source_system, "source_record_id": source.source_record_id, "source_version": source.source_version,
        "delivery_id": source.delivery_id, "status": source.status, "received_at": source.received_at, "source_event_time": source.source_event_time,
        "payload_hash": source.payload_hash, "quarantine_reason": source.quarantine_reason, "payload": decode_payload(source.payload)}
    lines = (await db.execute(select(TransactionLine).where(TransactionLine.transaction_id == tx.id).order_by(TransactionLine.line_no))).scalars().all()
    discounts = (await db.execute(select(TransactionDiscount).where(TransactionDiscount.transaction_id == tx.id).order_by(TransactionDiscount.line_no, TransactionDiscount.kind))).scalars().all()
    taxes = (await db.execute(select(TransactionTax).where(TransactionTax.transaction_id == tx.id))).scalars().all()
    tenders = (await db.execute(select(TransactionTender).where(TransactionTender.transaction_id == tx.id).order_by(TransactionTender.tender_type))).scalars().all()
    norm = {"lines": [{"gross_amount": l.gross_amount, "tax_amount": l.tax_amount} for l in lines], "discounts": [{"amount": d.amount} for d in discounts],
            "taxes": [{"tax_amount": t.tax_amount} for t in taxes], "tenders": [{"amount": t.amount, "tender_type": t.tender_type} for t in tenders]}
    summary = detail_math.check(norm if (lines or tenders) else None, tx.signed_amount)
    out["detail"] = {
        "status": summary["status"],
        "lines": [{"line_no": l.line_no, "item_code": l.item_code, "description": l.description, "quantity": l.quantity, "unit_price": l.unit_price,
                   "gross_amount": l.gross_amount, "tax_code": l.tax_code, "tax_rate": l.tax_rate, "tax_amount": l.tax_amount,
                   "discount_amount": sum((d.amount for d in discounts if d.line_no == l.line_no), Decimal("0")), "return_reason": l.return_reason} for l in lines],
        "discounts": [{"kind": d.kind, "code": d.code, "description": d.description, "line_no": d.line_no, "amount": d.amount} for d in discounts],
        "taxes": [{"tax_code": t.tax_code, "tax_name": t.tax_name, "rate": t.rate, "taxable_amount": t.taxable_amount, "tax_amount": t.tax_amount} for t in taxes],
        "tenders": [{"tender_type": t.tender_type, "amount": t.amount, "reference": t.reference, "authorization": t.authorization} for t in tenders],
        "totals": None if summary["status"] == "No detail" else {k: summary[k] for k in ("gross", "discounts", "tax", "net", "expected_total", "tendered") if k in summary},
        "total_variance": summary.get("total_variance"), "tender_variance": summary.get("tender_variance")}
    out["returns"] = await return_context(db, tx, lines) if (tx.transaction_type in ("Return", "Sale")) else None
    out["cases"], out["linked_transactions"] = cases, linked
    out["adjustments"] = [{"id": a.id, "adjustment_type": a.adjustment_type, "status": a.status, "rationale": a.rationale,
                           "proposed_by": a.proposed_by, "created_at": a.created_at} for a in adjustments]
    return out
