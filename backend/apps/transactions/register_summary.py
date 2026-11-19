"""Cashier and register activity summary for a single audited store day."""

from datetime import date
from typing import Annotated, Literal

from database import get_db
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import case, func, literal, select, union
from sqlalchemy.ext.asyncio import AsyncSession

from apps.cash.models import CashControl
from apps.csv_export import csv_table
from apps.ingest.models import RegisterEvent
from apps.security import scope
from apps.security.authentication import require_role
from apps.totals.currency import single_currency
from apps.transactions.models import CanonicalTransaction

router = APIRouter(prefix="/api/register-summary", tags=["Register summary"])
PAGE_SIZE_MAX = 200
CSV_MAX_ROWS = 100_000


def validate_day(value: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="business_date must be YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise HTTPException(status_code=422, detail="business_date must use YYYY-MM-DD")
    return value


@router.get("")
async def register_summary(
    store_id: Annotated[str, Query(min_length=1, max_length=50)],
    business_date: Annotated[str, Query(min_length=10, max_length=10)],
    group_by: Literal["register", "cashier"] = "register",
    format: Literal["json", "csv"] = "json",
    page: Annotated[int, Query(ge=1, le=100000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=PAGE_SIZE_MAX)] = 50,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("analyst", "finance", "it", "auditor")),
):
    validate_day(business_date)
    scope.require_store(user, store_id)
    currencies = (
        (
            await db.execute(
                select(CanonicalTransaction.currency)
                .where(
                    CanonicalTransaction.store_id == store_id,
                    CanonicalTransaction.business_date == business_date,
                    CanonicalTransaction.disposition != "Duplicate",
                )
                .distinct()
            )
        )
        .scalars()
        .all()
    )
    try:
        currency = single_currency(currencies)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    transaction_dimension = (
        CanonicalTransaction.register_id
        if group_by == "register"
        else CanonicalTransaction.cashier_id
    )
    event_dimension = (
        RegisterEvent.register_id if group_by == "register" else RegisterEvent.cashier_id
    )
    transaction_totals = (
        select(
            transaction_dimension.label("dimension"),
            CanonicalTransaction.currency.label("currency"),
            func.count(CanonicalTransaction.id).label("transaction_count"),
            func.count(case((CanonicalTransaction.reconciliation_status == "Exception", 1))).label(
                "exception_count"
            ),
            func.count(case((CanonicalTransaction.detail_status == "Unbalanced", 1))).label(
                "unbalanced_count"
            ),
            func.sum(
                case(
                    (CanonicalTransaction.signed_amount > 0, CanonicalTransaction.signed_amount),
                    else_=0,
                )
            ).label("sales_amount"),
            -func.sum(
                case(
                    (CanonicalTransaction.signed_amount < 0, CanonicalTransaction.signed_amount),
                    else_=0,
                )
            ).label("return_amount"),
            func.sum(CanonicalTransaction.signed_amount).label("net_amount"),
        )
        .where(
            CanonicalTransaction.store_id == store_id,
            CanonicalTransaction.business_date == business_date,
            CanonicalTransaction.disposition != "Duplicate",
        )
        .group_by(transaction_dimension, CanonicalTransaction.currency)
        .subquery()
    )
    event_totals = (
        select(
            event_dimension.label("dimension"),
            RegisterEvent.currency.label("currency"),
            func.count(RegisterEvent.id).label("register_event_count"),
            func.count(case((RegisterEvent.audit_code.is_not(None), 1))).label(
                "flagged_event_count"
            ),
        )
        .where(RegisterEvent.store_id == store_id, RegisterEvent.business_date == business_date)
        .group_by(event_dimension, RegisterEvent.currency)
        .subquery()
    )
    cash_totals = (
        select(
            CashControl.register_id.label("dimension")
            if group_by == "register"
            else CashControl.cashier_id.label("dimension"),
            literal(currency).label("currency"),
            func.count(CashControl.id).label("cash_declaration_count"),
            func.count(case((CashControl.variance_amount != 0, 1))).label("out_of_balance_count"),
            func.sum(
                case((CashControl.variance_amount > 0, CashControl.variance_amount), else_=0)
            ).label("short_amount"),
            -func.sum(
                case((CashControl.variance_amount < 0, CashControl.variance_amount), else_=0)
            ).label("over_amount"),
        )
        .where(
            CashControl.store_id == store_id,
            CashControl.business_date == business_date,
            CashControl.tender_type == "Cash",
        )
        .group_by(CashControl.register_id if group_by == "register" else CashControl.cashier_id)
        .subquery()
    )
    keys = union(
        select(transaction_totals.c.dimension, transaction_totals.c.currency),
        select(event_totals.c.dimension, event_totals.c.currency),
        select(cash_totals.c.dimension, cash_totals.c.currency),
    ).subquery()
    rows_query = (
        select(
            keys.c.dimension,
            keys.c.currency,
            func.coalesce(transaction_totals.c.transaction_count, 0).label("transaction_count"),
            func.coalesce(transaction_totals.c.exception_count, 0).label("exception_count"),
            func.coalesce(transaction_totals.c.unbalanced_count, 0).label("unbalanced_count"),
            func.coalesce(transaction_totals.c.sales_amount, 0).label("sales_amount"),
            func.coalesce(transaction_totals.c.return_amount, 0).label("return_amount"),
            func.coalesce(transaction_totals.c.net_amount, 0).label("net_amount"),
            func.coalesce(event_totals.c.register_event_count, 0).label("register_event_count"),
            func.coalesce(event_totals.c.flagged_event_count, 0).label("flagged_event_count"),
            func.coalesce(cash_totals.c.cash_declaration_count, 0).label("cash_declaration_count"),
            func.coalesce(cash_totals.c.out_of_balance_count, 0).label("out_of_balance_count"),
            func.coalesce(cash_totals.c.short_amount, 0).label("short_amount"),
            func.coalesce(cash_totals.c.over_amount, 0).label("over_amount"),
        )
        .select_from(keys)
        .outerjoin(
            transaction_totals,
            transaction_totals.c.dimension.is_not_distinct_from(keys.c.dimension)
            & (transaction_totals.c.currency == keys.c.currency),
        )
        .outerjoin(
            event_totals,
            event_totals.c.dimension.is_not_distinct_from(keys.c.dimension)
            & (event_totals.c.currency == keys.c.currency),
        )
        .outerjoin(
            cash_totals,
            cash_totals.c.dimension.is_not_distinct_from(keys.c.dimension)
            & (cash_totals.c.currency == keys.c.currency),
        )
    )
    total = (await db.execute(select(func.count()).select_from(rows_query.subquery()))).scalar_one()
    ordering = (
        func.coalesce(transaction_totals.c.exception_count, 0).desc(),
        keys.c.dimension,
        keys.c.currency,
    )
    if format == "csv":
        export_rows = (
            (await db.execute(rows_query.order_by(*ordering).limit(CSV_MAX_ROWS + 1)))
            .mappings()
            .all()
        )
        columns = (
            "dimension",
            "currency",
            "transaction_count",
            "exception_count",
            "unbalanced_count",
            "sales_amount",
            "return_amount",
            "net_amount",
            "register_event_count",
            "flagged_event_count",
            "cash_declaration_count",
            "out_of_balance_count",
            "short_amount",
            "over_amount",
        )
        headers = (
            "Register" if group_by == "register" else "Cashier",
            "Currency",
            "Transactions",
            "Exceptions",
            "Unbalanced transactions",
            "Sales amount",
            "Return amount",
            "Net sales amount",
            "Register events",
            "Flagged register events",
            "Cash declarations",
            "Out of balance declarations",
            "Short amount",
            "Over amount",
        )
        return Response(
            content=csv_table(
                headers,
                (
                    (row[column] if row[column] is not None else "" for column in columns)
                    for row in export_rows[:CSV_MAX_ROWS]
                ),
            ),
            media_type="text/csv",
            headers={"X-Report-Truncated": str(len(export_rows) > CSV_MAX_ROWS).lower()},
        )
    rows = (
        (
            await db.execute(
                rows_query.order_by(*ordering).offset((page - 1) * page_size).limit(page_size)
            )
        )
        .mappings()
        .all()
    )
    return {
        "store_id": store_id,
        "business_date": business_date,
        "group_by": group_by,
        "items": [dict(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, -(-total // page_size)),
    }
