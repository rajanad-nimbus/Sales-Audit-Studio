"""Current miscellaneous totals for store-day analysis."""

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
from apps.store_days.models import AuditTotal, StoreDay
from apps.totals.models import AuditTotalDefinition

router = APIRouter(prefix="/api/misc-totals", tags=["Miscellaneous totals"])
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
async def miscellaneous_totals(
    store_id: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
    date_from: Annotated[str | None, Query(max_length=10)] = None,
    date_to: Annotated[str | None, Query(max_length=10)] = None,
    q: Annotated[str | None, Query(max_length=120)] = None,
    format: Literal["json", "csv"] = "json",
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("analyst", "finance", "it", "auditor")),
):
    date_from = valid_date(date_from, "date_from")
    date_to = valid_date(date_to, "date_to")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must not be after date_to")

    filters = [
        StoreDay.audit_version >= 1,
        AuditTotal.audit_version == StoreDay.audit_version,
        AuditTotalDefinition.category == "Miscellaneous",
    ]
    if store_id:
        filters.append(StoreDay.store_id == store_id)
    if date_from:
        filters.append(StoreDay.business_date >= date_from)
    if date_to:
        filters.append(StoreDay.business_date <= date_to)
    if q and q.strip():
        filters.append(
            contains_literal(AuditTotal.total_name, q)
            | contains_literal(AuditTotal.dimension_key, q)
            | contains_literal(AuditTotal.rollup_key, q)
        )

    query = (
        select(
            AuditTotal,
            StoreDay.store_id,
            StoreDay.business_date,
            StoreDay.status.label("store_day_status"),
            StoreDay.totals_stale,
        )
        .join(StoreDay, StoreDay.id == AuditTotal.store_day_id)
        .join(AuditTotalDefinition, AuditTotalDefinition.id == AuditTotal.definition_id)
        .where(*filters)
    )
    query = scope.restrict(query, user, StoreDay.store_id)
    counted = select(func.count()).select_from(query.order_by(None).subquery())
    total = (await db.execute(counted)).scalar_one()
    summary_query = (
        select(
            func.count(func.distinct(StoreDay.id)),
            func.count(
                func.distinct(case((StoreDay.totals_stale.is_(True), StoreDay.id), else_=None))
            ),
        )
        .select_from(AuditTotal)
        .join(StoreDay, StoreDay.id == AuditTotal.store_day_id)
        .join(AuditTotalDefinition, AuditTotalDefinition.id == AuditTotal.definition_id)
        .where(*filters)
    )
    summary = (await db.execute(scope.restrict(summary_query, user, StoreDay.store_id))).one()
    store_days, stale_store_days = summary

    columns = (
        "store_id",
        "business_date",
        "store_day_status",
        "totals_stale",
        "version",
        "total_name",
        "level",
        "dimension",
        "rollup_key",
        "currency",
        "calculated_amount",
        "declared_amount",
        "variance_amount",
        "definition_revision",
    )

    def result_row(row):
        total_row = row[0]
        return {
            "store_id": row.store_id,
            "business_date": row.business_date,
            "store_day_status": row.store_day_status,
            "totals_stale": row.totals_stale,
            "version": total_row.audit_version,
            "total_name": total_row.total_name,
            "level": total_row.level,
            "dimension": total_row.dimension_key,
            "rollup_key": total_row.rollup_key,
            "currency": total_row.currency,
            "calculated_amount": total_row.calculated_amount,
            "declared_amount": total_row.declared_amount,
            "variance_amount": total_row.variance_amount,
            "definition_revision": total_row.definition_revision,
        }

    if format == "csv":
        rows = (
            await db.execute(
                query.order_by(
                    StoreDay.business_date.desc(),
                    StoreDay.store_id,
                    AuditTotal.total_name,
                    AuditTotal.level,
                    AuditTotal.dimension_key,
                    AuditTotal.rollup_key,
                ).limit(CSV_MAX_ROWS + 1)
            )
        ).all()
        content = csv_table(
            columns,
            ((result_row(row)[column] for column in columns) for row in rows[:CSV_MAX_ROWS]),
        )
        return Response(
            content=content,
            media_type="text/csv",
            headers={"X-Report-Truncated": str(len(rows) > CSV_MAX_ROWS).lower()},
        )

    rows = (
        await db.execute(
            query.order_by(
                StoreDay.business_date.desc(),
                StoreDay.store_id,
                AuditTotal.total_name,
                AuditTotal.level,
                AuditTotal.dimension_key,
                AuditTotal.rollup_key,
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return {
        "items": [result_row(row) for row in rows],
        "total": total,
        "store_days": store_days,
        "stale_store_days": stale_store_days,
        "page": page,
        "page_size": page_size,
        "pages": max(1, -(-total // page_size)),
    }
