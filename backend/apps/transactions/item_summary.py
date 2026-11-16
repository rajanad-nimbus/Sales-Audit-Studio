"""Paged item-level sales, return, and tax summary for store audit review."""

from datetime import date
from typing import Annotated, Literal

from database import get_db
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.csv_export import csv_table
from apps.search import contains_literal
from apps.security import scope
from apps.security.authentication import require_role
from apps.transactions.models import CanonicalTransaction, TransactionLine

router = APIRouter(prefix="/api/item-summary", tags=["Item summary"])
MAX_PAGE_SIZE = 200
CSV_MAX_ROWS = 100_000


def valid_date(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{field} must be YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise HTTPException(status_code=422, detail=f"{field} must use YYYY-MM-DD")
    return value


@router.get("")
async def item_summary(
    store_id: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
    date_from: Annotated[str | None, Query(max_length=10)] = None,
    date_to: Annotated[str | None, Query(max_length=10)] = None,
    q: Annotated[str | None, Query(max_length=120)] = None,
    format: Literal["json", "csv"] = "json",
    page: Annotated[int, Query(ge=1, le=100000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("analyst", "finance", "it", "auditor")),
):
    date_from = valid_date(date_from, "date_from")
    date_to = valid_date(date_to, "date_to")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must not be after date_to")
    filters = []
    if store_id:
        filters.append(CanonicalTransaction.store_id == store_id)
    if date_from:
        filters.append(CanonicalTransaction.business_date >= date_from)
    if date_to:
        filters.append(CanonicalTransaction.business_date <= date_to)
    if q and q.strip():
        filters.append(
            contains_literal(TransactionLine.item_code, q)
            | contains_literal(TransactionLine.description, q)
        )

    sold_quantity = func.sum(
        case((TransactionLine.quantity > 0, TransactionLine.quantity), else_=0)
    )
    returned_quantity = -func.sum(
        case((TransactionLine.quantity < 0, TransactionLine.quantity), else_=0)
    )
    sold_amount = func.sum(
        case((TransactionLine.gross_amount > 0, TransactionLine.gross_amount), else_=0)
    )
    returned_amount = -func.sum(
        case((TransactionLine.gross_amount < 0, TransactionLine.gross_amount), else_=0)
    )
    line_query = (
        select(
            TransactionLine.item_code.label("item_code"),
            func.max(TransactionLine.description).label("description"),
            CanonicalTransaction.currency.label("currency"),
            func.count(func.distinct(CanonicalTransaction.id)).label("transaction_count"),
            sold_quantity.label("sold_quantity"),
            returned_quantity.label("returned_quantity"),
            (sold_quantity - returned_quantity).label("net_quantity"),
            sold_amount.label("sold_amount"),
            returned_amount.label("returned_amount"),
            func.sum(TransactionLine.gross_amount).label("net_sales"),
            func.sum(TransactionLine.tax_amount).label("net_tax"),
        )
        .join(CanonicalTransaction, CanonicalTransaction.id == TransactionLine.transaction_id)
        .where(*filters)
        .group_by(TransactionLine.item_code, CanonicalTransaction.currency)
    )
    grouped = scope.restrict(line_query, user, CanonicalTransaction.store_id).subquery()
    total = (await db.execute(select(func.count()).select_from(grouped))).scalar_one()
    if format == "csv":
        export_rows = (
            (
                await db.execute(
                    select(grouped)
                    .order_by(grouped.c.item_code, grouped.c.currency)
                    .limit(CSV_MAX_ROWS + 1)
                )
            )
            .mappings()
            .all()
        )
        columns = (
            "item_code",
            "description",
            "currency",
            "transaction_count",
            "sold_quantity",
            "returned_quantity",
            "net_quantity",
            "sold_amount",
            "returned_amount",
            "net_sales",
            "net_tax",
        )
        return Response(
            content=csv_table(
                columns, ((row[column] for column in columns) for row in export_rows[:CSV_MAX_ROWS])
            ),
            media_type="text/csv",
            headers={"X-Report-Truncated": str(len(export_rows) > CSV_MAX_ROWS).lower()},
        )
    rows = (
        (
            await db.execute(
                select(grouped)
                .order_by(grouped.c.item_code, grouped.c.currency)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .mappings()
        .all()
    )
    summary_rows = (
        (
            await db.execute(
                select(
                    grouped.c.currency,
                    func.sum(grouped.c.sold_amount).label("sold_amount"),
                    func.sum(grouped.c.returned_amount).label("returned_amount"),
                    func.sum(grouped.c.net_sales).label("net_sales"),
                    func.sum(grouped.c.net_tax).label("net_tax"),
                )
                .group_by(grouped.c.currency)
                .order_by(grouped.c.currency)
            )
        )
        .mappings()
        .all()
    )
    return {
        "items": [dict(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, -(-total // page_size)),
        "totals_by_currency": [dict(row) for row in summary_rows],
    }
