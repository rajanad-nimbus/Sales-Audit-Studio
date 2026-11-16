"""Searchable history of calculated totals across store-day versions."""

from datetime import date
from typing import Annotated

from database import get_db
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.csv_export import csv_table
from apps.search import contains_literal
from apps.security import scope
from apps.security.authentication import require_role
from apps.store_days.models import AuditTotal, StoreDay

router = APIRouter(prefix="/api/total-history", tags=["Total audit trail"])
MAX_PAGE_SIZE = 200
EXPORT_PAGE_SIZE = 1000
MAX_EXPORT_ROWS = 50_000


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


def history_query(
    *,
    store_id: str | None,
    date_from: str | None,
    date_to: str | None,
    total_name: str | None,
    level: str | None,
    dimension: str | None,
    min_version: int | None,
    user: dict,
):
    """Build the searchable total history query with the caller's store scope applied."""
    previous = AuditTotal.__table__.alias("previous_total")
    previous_join = and_(
        previous.c.store_day_id == AuditTotal.store_day_id,
        previous.c.audit_version == AuditTotal.audit_version - 1,
        previous.c.total_name == AuditTotal.total_name,
        previous.c.level == AuditTotal.level,
        previous.c.dimension_key.is_not_distinct_from(AuditTotal.dimension_key),
        previous.c.rollup_key.is_not_distinct_from(AuditTotal.rollup_key),
    )
    query = (
        select(
            AuditTotal,
            StoreDay.store_id,
            StoreDay.business_date,
            StoreDay.status,
            StoreDay.audit_version.label("current_version"),
            previous.c.calculated_amount.label("previous_amount"),
            previous.c.definition_revision.label("previous_definition_revision"),
        )
        .join(StoreDay, StoreDay.id == AuditTotal.store_day_id)
        .outerjoin(previous, previous_join)
    )
    filters = [AuditTotal.audit_version >= 1]
    if store_id:
        filters.append(StoreDay.store_id == store_id)
    if date_from:
        filters.append(StoreDay.business_date >= date_from)
    if date_to:
        filters.append(StoreDay.business_date <= date_to)
    if total_name and total_name.strip():
        filters.append(contains_literal(AuditTotal.total_name, total_name))
    if level:
        filters.append(AuditTotal.level == level)
    if dimension and dimension.strip():
        filters.append(contains_literal(AuditTotal.dimension_key, dimension))
    if min_version is not None:
        filters.append(AuditTotal.audit_version >= min_version)
    return scope.restrict(query.where(*filters), user, StoreDay.store_id)


def history_ordering():
    return (
        StoreDay.business_date.desc(),
        StoreDay.store_id,
        AuditTotal.total_name,
        AuditTotal.level,
        AuditTotal.dimension_key,
        AuditTotal.rollup_key,
        AuditTotal.audit_version.desc(),
    )


def history_item(row) -> dict:
    total_row = row[0]
    previous_amount = row.previous_amount
    change = total_row.calculated_amount - previous_amount if previous_amount is not None else None
    return {
        "store_id": row.store_id,
        "business_date": row.business_date,
        "store_day_status": row.status,
        "current_version": row.current_version,
        "version": total_row.audit_version,
        "total_name": total_row.total_name,
        "level": total_row.level,
        "dimension": total_row.dimension_key,
        "rollup_key": total_row.rollup_key,
        "currency": total_row.currency,
        "calculated_amount": total_row.calculated_amount,
        "declared_amount": total_row.declared_amount,
        "variance_amount": total_row.variance_amount,
        "previous_amount": previous_amount,
        "change_amount": change,
        "definition_revision": total_row.definition_revision,
        "previous_definition_revision": row.previous_definition_revision,
        "created_at": total_row.created_at,
    }


def history_csv_row(row) -> list:
    item = history_item(row)
    return [
        item["store_id"],
        item["business_date"],
        item["store_day_status"],
        item["current_version"],
        item["version"],
        item["total_name"],
        item["level"],
        item["dimension"] or "",
        item["rollup_key"] or "",
        item["currency"],
        item["calculated_amount"],
        item["declared_amount"] if item["declared_amount"] is not None else "",
        item["variance_amount"] if item["variance_amount"] is not None else "",
        item["previous_amount"] if item["previous_amount"] is not None else "",
        item["change_amount"] if item["change_amount"] is not None else "",
        item["definition_revision"] if item["definition_revision"] is not None else "",
        (
            item["previous_definition_revision"]
            if item["previous_definition_revision"] is not None
            else ""
        ),
        item["created_at"],
    ]


@router.get("")
async def search_total_history(
    store_id: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
    date_from: Annotated[str | None, Query(max_length=10)] = None,
    date_to: Annotated[str | None, Query(max_length=10)] = None,
    total_name: Annotated[str | None, Query(max_length=100)] = None,
    level: Annotated[str | None, Query(max_length=24)] = None,
    dimension: Annotated[str | None, Query(max_length=100)] = None,
    min_version: Annotated[int | None, Query(ge=1)] = None,
    page: Annotated[int, Query(ge=1, le=100000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("analyst", "finance", "it", "auditor")),
):
    date_from = valid_date(date_from, "date_from")
    date_to = valid_date(date_to, "date_to")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must not be after date_to")

    query = history_query(
        store_id=store_id,
        date_from=date_from,
        date_to=date_to,
        total_name=total_name,
        level=level,
        dimension=dimension,
        min_version=min_version,
        user=user,
    )

    count = await db.execute(select(func.count()).select_from(query.order_by(None).subquery()))
    total = count.scalar_one()
    rows = (
        await db.execute(
            query.order_by(*history_ordering())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    items = [history_item(row) for row in rows]
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, -(-total // page_size)),
    }


@router.get("/export.csv")
async def export_total_history(
    store_id: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
    date_from: Annotated[str | None, Query(max_length=10)] = None,
    date_to: Annotated[str | None, Query(max_length=10)] = None,
    total_name: Annotated[str | None, Query(max_length=100)] = None,
    level: Annotated[str | None, Query(max_length=24)] = None,
    dimension: Annotated[str | None, Query(max_length=100)] = None,
    min_version: Annotated[int | None, Query(ge=1)] = None,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("analyst", "finance", "it", "auditor")),
):
    """Export all matching total revisions, enforcing the same store scope as search."""
    date_from = valid_date(date_from, "date_from")
    date_to = valid_date(date_to, "date_to")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must not be after date_to")
    query = history_query(
        store_id=store_id,
        date_from=date_from,
        date_to=date_to,
        total_name=total_name,
        level=level,
        dimension=dimension,
        min_version=min_version,
        user=user,
    )
    count = await db.execute(select(func.count()).select_from(query.order_by(None).subquery()))
    total = count.scalar_one()
    if total > MAX_EXPORT_ROWS:
        raise HTTPException(
            status_code=413,
            detail=f"The filtered total history exceeds {MAX_EXPORT_ROWS:,} rows. Narrow the filters and try again.",
        )

    exported = []
    for offset in range(0, total, EXPORT_PAGE_SIZE):
        rows = (
            await db.execute(
                query.order_by(*history_ordering()).offset(offset).limit(EXPORT_PAGE_SIZE)
            )
        ).all()
        exported.extend(history_csv_row(row) for row in rows)
    return Response(
        csv_table(
            [
                "Store",
                "Business date",
                "Store day status",
                "Current version",
                "Version",
                "Total",
                "Level",
                "Dimension",
                "Rollup key",
                "Currency",
                "Calculated",
                "Declared",
                "Variance",
                "Previous calculated",
                "Change vs previous",
                "Definition revision",
                "Previous definition revision",
                "Recorded at",
            ],
            exported,
        ),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=total-audit-history.csv"},
    )
