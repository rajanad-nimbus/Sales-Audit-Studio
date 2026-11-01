"""Browse every canonical transaction, with the raw source record and the cases it relates to."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from database import get_db
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import String, cast, func, literal, or_, select, union
from sqlalchemy import case as sql_case
from sqlalchemy.ext.asyncio import AsyncSession

from apps.ingest.models import SourceRecord
from apps.search import contains_literal
from apps.security import scope
from apps.security.authentication import require_role
from apps.transactions.detail_routes import (
    MAX_PAYLOAD_CHARS as MAX_PAYLOAD_CHARS,
)
from apps.transactions.detail_routes import (
    MAX_RELATED_CASES as MAX_RELATED_CASES,
)
from apps.transactions.detail_routes import (
    MAX_RELATED_RETURNS as MAX_RELATED_RETURNS,
)
from apps.transactions.detail_routes import (
    MAX_RETURN_ITEM_ROWS as MAX_RETURN_ITEM_ROWS,
)
from apps.transactions.detail_routes import (
    MAX_TRANSACTION_ADJUSTMENTS as MAX_TRANSACTION_ADJUSTMENTS,
)
from apps.transactions.detail_routes import (
    MAX_TRANSACTION_DETAIL_ROWS as MAX_TRANSACTION_DETAIL_ROWS,
)
from apps.transactions.detail_routes import (
    decode_payload as decode_payload,
)
from apps.transactions.detail_routes import (
    fmt_qty as fmt_qty,
)
from apps.transactions.detail_routes import (
    return_context as return_context,
)
from apps.transactions.detail_routes import (
    router as detail_router,
)
from apps.transactions.detail_routes import (
    transaction_detail as transaction_detail,
)
from apps.transactions.models import CanonicalTransaction, TransactionLine
from apps.transactions.views import row_view as row_view

router = APIRouter(prefix="/api/transactions", tags=["Transactions"])
SORTS = {
    "business_date": CanonicalTransaction.business_date,
    "event_timestamp": CanonicalTransaction.event_timestamp,
    "amount": CanonicalTransaction.signed_amount,
    "store": CanonicalTransaction.store_id,
    "type": CanonicalTransaction.transaction_type,
    "status": CanonicalTransaction.reconciliation_status,
    "tender": CanonicalTransaction.tender_type,
    "source": SourceRecord.source_system,
}


def conditions(
    store_id=None,
    source_system=None,
    transaction_type=None,
    tender_type=None,
    status=None,
    date_from=None,
    date_to=None,
    q=None,
    skip_status=False,
    detail_status=None,
) -> list:
    c = []
    if store_id:
        c.append(CanonicalTransaction.store_id == store_id)
    if source_system:
        c.append(SourceRecord.source_system == source_system)
    if transaction_type:
        c.append(CanonicalTransaction.transaction_type == transaction_type)
    if tender_type:
        c.append(CanonicalTransaction.tender_type == tender_type)
    if status and not skip_status:
        c.append(CanonicalTransaction.reconciliation_status == status)
    if detail_status:
        c.append(CanonicalTransaction.detail_status == detail_status)
    if date_from:
        c.append(CanonicalTransaction.business_date >= date_from)
    if date_to:
        c.append(CanonicalTransaction.business_date <= date_to)
    if q and q.strip():
        c.append(
            or_(
                contains_literal(CanonicalTransaction.id, q),
                contains_literal(CanonicalTransaction.payment_reference, q),
                contains_literal(CanonicalTransaction.settlement_reference, q),
                contains_literal(CanonicalTransaction.register_id, q),
                contains_literal(SourceRecord.source_record_id, q),
            )
        )
    return c


def canonical_date_filter(value: date | str | None, field: str) -> str | None:
    """Normalize a query date and reject impossible or noncanonical calendar values."""
    if value is None:
        return None
    try:
        parsed = value if isinstance(value, date) else date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422, detail=f"{field} must be a valid YYYY-MM-DD date"
        ) from exc
    normalized = parsed.isoformat()
    if isinstance(value, str) and normalized != value:
        raise HTTPException(status_code=422, detail=f"{field} must use YYYY-MM-DD")
    return normalized


@router.get("")
async def list_transactions(
    store_id: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
    source_system: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
    transaction_type: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
    tender_type: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
    status: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
    date_from: date | None = None,
    date_to: date | None = None,
    q: Annotated[str | None, Query(max_length=255)] = None,
    detail_status: Annotated[str | None, Query(min_length=1, max_length=20)] = None,
    sort: Literal[
        "business_date", "event_timestamp", "amount", "store", "type", "status", "tender", "source"
    ] = "business_date",
    dir: Literal["asc", "desc"] = "desc",
    page: Annotated[int, Query(ge=1, le=10000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("analyst", "finance", "it", "auditor")),
):
    date_from = canonical_date_filter(date_from, "date_from")
    date_to = canonical_date_filter(date_to, "date_to")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must not be after date_to")
    base = select(CanonicalTransaction, SourceRecord).outerjoin(
        SourceRecord, CanonicalTransaction.source_record_id == SourceRecord.id
    )
    where = conditions(
        store_id,
        source_system,
        transaction_type,
        tender_type,
        status,
        date_from,
        date_to,
        q,
        detail_status=detail_status,
    )
    # Status tiles intentionally ignore the selected status, while the result count
    # and net amount honor it. Compute both views in one grouped database pass.
    spread_where = conditions(
        store_id,
        source_system,
        transaction_type,
        tender_type,
        status,
        date_from,
        date_to,
        q,
        skip_status=True,
        detail_status=detail_status,
    )
    selected_status = CanonicalTransaction.reconciliation_status == status if status else None
    selected_count = (
        func.count()
        if selected_status is None
        else func.sum(sql_case((selected_status, 1), else_=0))
    )
    transaction_amount = (
        CanonicalTransaction.signed_amount
        if selected_status is None
        else sql_case((selected_status, CanonicalTransaction.signed_amount), else_=0)
    )
    aggregate_query = (
        select(
            CanonicalTransaction.reconciliation_status,
            func.count().label("status_count"),
            selected_count.label("selected_count"),
            func.coalesce(func.sum(transaction_amount), 0).label("net_amount"),
        )
        .select_from(CanonicalTransaction)
        .outerjoin(SourceRecord, CanonicalTransaction.source_record_id == SourceRecord.id)
        .where(*spread_where)
        .group_by(CanonicalTransaction.reconciliation_status)
    )
    aggregate_rows = (
        await db.execute(scope.restrict(aggregate_query, _, CanonicalTransaction.store_id))
    ).all()
    by_status = {status: count for status, count, _, _ in aggregate_rows}
    total = sum(count for _, _, count, _ in aggregate_rows)
    net = sum((amount for _, _, _, amount in aggregate_rows), Decimal("0"))
    col = SORTS.get(sort, CanonicalTransaction.business_date)
    order = col.desc().nulls_last() if dir == "desc" else col.asc().nulls_last()
    rows_query = base.where(*where).order_by(
        order, CanonicalTransaction.event_timestamp.desc(), CanonicalTransaction.id
    )
    rows_query = scope.restrict(rows_query, _, CanonicalTransaction.store_id)
    rows = (await db.execute(rows_query.offset((page - 1) * page_size).limit(page_size))).all()
    counts = (
        dict(
            (
                await db.execute(
                    select(TransactionLine.transaction_id, func.count())
                    .where(TransactionLine.transaction_id.in_([t.id for t, _ in rows]))
                    .group_by(TransactionLine.transaction_id)
                )
            ).all()
        )
        if rows
        else {}
    )
    return {
        "items": [row_view(t, s, counts.get(t.id, 0)) for t, s in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, -(-total // page_size)),
        "by_status": by_status,
        "net_amount": str(net),
    }


@router.get("/facets")
async def facets(
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("analyst", "finance", "it", "auditor")),
):
    facet_columns = {
        "stores": CanonicalTransaction.store_id,
        "sources": SourceRecord.source_system,
        "types": CanonicalTransaction.transaction_type,
        "tenders": CanonicalTransaction.tender_type,
        "statuses": CanonicalTransaction.reconciliation_status,
        "detail_statuses": CanonicalTransaction.detail_status,
    }
    facet_queries = []
    for name, column in facet_columns.items():
        query = select(literal(name).label("name"), cast(column, String).label("value"))
        query = (
            query.select_from(CanonicalTransaction)
            .outerjoin(
                SourceRecord,
                CanonicalTransaction.source_record_id == SourceRecord.id,
            )
            .where(column.is_not(None))
        )
        facet_queries.append(scope.restrict(query, _, CanonicalTransaction.store_id))
    facet_values = union(*facet_queries).subquery()
    facet_rows = (await db.execute(select(facet_values.c.name, facet_values.c.value))).all()
    # Sort the one UNION result per facet without issuing one DISTINCT query per column.
    facets_by_name = {name: [] for name in facet_columns}
    for name, value in sorted(facet_rows):
        facets_by_name[name].append(value)
    date_query = scope.restrict(
        select(
            func.min(CanonicalTransaction.business_date),
            func.max(CanonicalTransaction.business_date),
        ),
        _,
        CanonicalTransaction.store_id,
    )
    lo, hi = (await db.execute(date_query)).one()
    return {**facets_by_name, "date_min": lo, "date_max": hi}


router.include_router(detail_router)
