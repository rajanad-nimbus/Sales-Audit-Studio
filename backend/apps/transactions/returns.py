"""Return and refund lineage calculations for transaction detail views."""

from collections import defaultdict
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import aliased

from apps.ingest.models import SourceRecord
from apps.transactions.limits import MAX_RELATED_RETURNS, MAX_RETURN_ITEM_ROWS
from apps.transactions.models import CanonicalTransaction, TransactionLine


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
