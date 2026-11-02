"""Browse register events: voids, no-sales, paid-in/out, pulls, loans, and till, register and store activity."""

from datetime import date
from typing import Annotated, Literal

from database import get_db
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.ingest.models import RegisterEvent
from apps.search import contains_literal
from apps.security import scope
from apps.security.authentication import require_role

router = APIRouter(prefix="/api/register-events", tags=["Register events"])

ROLES = ("analyst", "finance", "it", "auditor")
SORTS = {
    "business_date": RegisterEvent.business_date,
    "time": RegisterEvent.event_time,
    "type": RegisterEvent.event_type,
    "store": RegisterEvent.store_id,
    "amount": RegisterEvent.amount,
}


def view(row: RegisterEvent) -> dict:
    return {
        "id": row.id,
        "business_date": row.business_date,
        "event_time": row.event_time.isoformat(),
        "store_id": row.store_id,
        "register_id": row.register_id,
        "cashier_id": row.cashier_id,
        "event_type": row.event_type,
        "sub_type": row.sub_type,
        "transaction_number": row.transaction_number,
        "original_transaction_number": row.original_transaction_number,
        "reason_code": row.reason_code,
        "amount": str(row.amount) if row.amount is not None else None,
        "currency": row.currency,
        "audit_code": row.audit_code,
    }


def conditions(store_id, event_type, flagged, date_from, date_to, q, skip_type=False) -> list:
    c = []
    if store_id:
        c.append(RegisterEvent.store_id == store_id)
    if event_type and not skip_type:
        c.append(RegisterEvent.event_type == event_type)
    if flagged:
        c.append(RegisterEvent.audit_code.is_not(None))
    if date_from:
        c.append(RegisterEvent.business_date >= date_from)
    if date_to:
        c.append(RegisterEvent.business_date <= date_to)
    if q and q.strip():
        c.append(
            or_(
                contains_literal(RegisterEvent.cashier_id, q.strip()),
                contains_literal(RegisterEvent.register_id, q.strip()),
                contains_literal(RegisterEvent.transaction_number, q.strip()),
                contains_literal(RegisterEvent.reason_code, q.strip()),
            )
        )
    return c


@router.get("")
async def list_register_events(
    store_id: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
    event_type: Annotated[str | None, Query(min_length=1, max_length=30)] = None,
    flagged: bool = False,
    date_from: date | None = None,
    date_to: date | None = None,
    q: Annotated[str | None, Query(max_length=255)] = None,
    sort: Literal["business_date", "time", "type", "store", "amount"] = "business_date",
    dir: Literal["asc", "desc"] = "desc",
    page: Annotated[int, Query(ge=1, le=10000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role(*ROLES)),
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must not be after date_to")
    start, end = (d.isoformat() if d else None for d in (date_from, date_to))
    where = conditions(store_id, event_type, flagged, start, end, q)
    # The type tiles ignore the selected type so they stay available as a way to change it.
    spread = conditions(store_id, event_type, flagged, start, end, q, skip_type=True)
    by_type = dict(
        (
            await db.execute(
                scope.restrict(
                    select(RegisterEvent.event_type, func.count())
                    .where(*spread)
                    .group_by(RegisterEvent.event_type),
                    user,
                    RegisterEvent.store_id,
                )
            )
        ).all()
    )
    flagged_count = (
        await db.execute(
            scope.restrict(
                select(func.count())
                .select_from(RegisterEvent)
                .where(*where, RegisterEvent.audit_code.is_not(None)),
                user,
                RegisterEvent.store_id,
            )
        )
    ).scalar_one()
    total = (
        await db.execute(
            scope.restrict(
                select(func.count()).select_from(RegisterEvent).where(*where),
                user,
                RegisterEvent.store_id,
            )
        )
    ).scalar_one()
    column = SORTS[sort]
    order = column.desc().nulls_last() if dir == "desc" else column.asc().nulls_last()
    rows = (
        (
            await db.execute(
                scope.restrict(select(RegisterEvent).where(*where), user, RegisterEvent.store_id)
                .order_by(order, RegisterEvent.event_time.desc(), RegisterEvent.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [view(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, -(-total // page_size)),
        "by_type": by_type,
        "flagged": flagged_count,
    }


@router.get("/facets")
async def facets(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role(*ROLES)),
):
    def restricted(query):
        return scope.restrict(query, user, RegisterEvent.store_id)

    stores = (
        (await db.execute(restricted(select(RegisterEvent.store_id).distinct()))).scalars().all()
    )
    types = (
        (await db.execute(restricted(select(RegisterEvent.event_type).distinct()))).scalars().all()
    )
    lo, hi = (
        await db.execute(
            restricted(
                select(func.min(RegisterEvent.business_date), func.max(RegisterEvent.business_date))
            )
        )
    ).one()
    return {"stores": sorted(stores), "types": sorted(types), "date_min": lo, "date_max": hi}
