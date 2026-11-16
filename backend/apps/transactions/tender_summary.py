"""Tender-level payment summary for store audit review."""

from datetime import date
from typing import Annotated, Literal

from database import get_db
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.csv_export import csv_table
from apps.search import contains_literal
from apps.security import scope
from apps.security.authentication import require_role
from apps.transactions.models import CanonicalTransaction, TransactionTender

router = APIRouter(prefix="/api/tender-summary", tags=["Tender summary"])
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
async def tender_summary(
    store_id: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
    date_from: Annotated[str | None, Query(max_length=10)] = None,
    date_to: Annotated[str | None, Query(max_length=10)] = None,
    q: Annotated[str | None, Query(max_length=120)] = None,
    format: Literal["json", "csv"] = "json",
    page: Annotated[int, Query(ge=1, le=10000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
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
            or_(
                contains_literal(TransactionTender.tender_type, q),
                contains_literal(CanonicalTransaction.transaction_type, q),
                contains_literal(CanonicalTransaction.payment_reference, q),
                contains_literal(CanonicalTransaction.settlement_reference, q),
            )
        )
    query = (
        select(
            TransactionTender.tender_type.label("tender_type"),
            CanonicalTransaction.currency.label("currency"),
            func.count(TransactionTender.id).label("tender_count"),
            func.count(func.distinct(CanonicalTransaction.id)).label("transaction_count"),
            func.sum(case((TransactionTender.amount > 0, TransactionTender.amount), else_=0)).label(
                "received_amount"
            ),
            -func.sum(
                case((TransactionTender.amount < 0, TransactionTender.amount), else_=0)
            ).label("refunded_amount"),
            func.sum(TransactionTender.amount).label("net_amount"),
        )
        .join(CanonicalTransaction, CanonicalTransaction.id == TransactionTender.transaction_id)
        .where(*filters)
        .group_by(TransactionTender.tender_type, CanonicalTransaction.currency)
    )
    grouped = scope.restrict(query, user, CanonicalTransaction.store_id).subquery()
    total = (await db.execute(select(func.count()).select_from(grouped))).scalar_one()
    if format == "csv":
        export_rows = (
            (
                await db.execute(
                    select(grouped)
                    .order_by(grouped.c.tender_type, grouped.c.currency)
                    .limit(CSV_MAX_ROWS + 1)
                )
            )
            .mappings()
            .all()
        )
        columns = (
            "tender_type",
            "currency",
            "tender_count",
            "transaction_count",
            "received_amount",
            "refunded_amount",
            "net_amount",
        )
        return Response(
            content=csv_table(
                columns, ((row[column] for column in columns) for row in export_rows[:CSV_MAX_ROWS])
            ),
            media_type="text/csv",
            headers={"X-Report-Truncated": str(len(export_rows) > CSV_MAX_ROWS).lower()},
        )
    items = (
        (
            await db.execute(
                select(grouped)
                .order_by(grouped.c.tender_type, grouped.c.currency)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .mappings()
        .all()
    )
    return {
        "items": [dict(row) for row in items],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, -(-total // page_size)),
    }
