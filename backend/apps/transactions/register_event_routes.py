"""Browse register events: voids, no-sales, paid-in/out, pulls, loans, and till, register and store activity."""

import json
import uuid
from datetime import date
from typing import Annotated, Literal

from database import get_db
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from model_base import utcnow
from pydantic import BaseModel, Field
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.audit.models import AuditEvent
from apps.csv_export import csv_table
from apps.ingest.models import RegisterEvent, RegisterEventAdjustment
from apps.search import contains_literal
from apps.security import scope
from apps.security.authentication import require_role
from apps.security.sensitive_data import redact_sensitive_fields
from apps.system.options import credit_card_number_mask

router = APIRouter(prefix="/api/register-events", tags=["Register events"])
CSV_MAX_ROWS = 100_000
PAID_OUT_FIELDS = {
    "vendor_number": 10,
    "vendor_invoice_number": 30,
    "proof_of_delivery_number": 30,
    "payment_reference_number": 16,
}
PAID_OUT_CORRECTION_EXPORT_MAX = 50_000

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
        "details": redact_sensitive_fields(row.details or {}),
        "amount": str(row.amount) if row.amount is not None else None,
        "currency": row.currency,
        "audit_code": row.audit_code,
    }


class PaidOutCorrectionInput(BaseModel):
    proposed_values: dict = Field(min_length=1, max_length=4)
    rationale: str = Field(min_length=1, max_length=2000)


class CorrectionRejectionInput(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


def adjustment_view(row: RegisterEventAdjustment) -> dict:
    return {
        "id": row.id,
        "event_id": row.event_id,
        "proposed_values": redact_sensitive_fields(row.proposed_values),
        "rationale": row.rationale,
        "status": row.status,
        "proposed_by": row.proposed_by,
        "reviewed_by": row.reviewed_by,
        "reviewed_at": row.reviewed_at,
        "created_at": row.created_at,
    }


def audit_event(db, event_type: str, row, actor: str, description: str, before=None, after=None):
    db.add(
        AuditEvent(
            id=str(uuid.uuid4()),
            event_type=event_type,
            actor=actor,
            object_type="RegisterEventAdjustment",
            object_id=row.id,
            action_description=description,
            before_state=redact_sensitive_fields(before) if before else None,
            after_state=redact_sensitive_fields(after) if after else None,
        )
    )


def validate_paid_out_values(values: dict) -> None:
    if not isinstance(values, dict) or not values or set(values) - PAID_OUT_FIELDS.keys():
        raise HTTPException(status_code=422, detail="Unsupported paid-out detail fields")
    for field, max_length in PAID_OUT_FIELDS.items():
        if field not in values:
            continue
        value = values[field]
        if value is not None and (
            not isinstance(value, str)
            or value != value.strip()
            or not value
            or len(value) > max_length
        ):
            raise HTTPException(
                status_code=422,
                detail=f"{field} must be nonempty text of at most {max_length} characters, or null to clear it",
            )


def conditions(
    store_id,
    event_type,
    flagged,
    date_from,
    date_to,
    q,
    register_id=None,
    cashier_id=None,
    skip_type=False,
    event_id=None,
) -> list:
    c = []
    if store_id:
        c.append(RegisterEvent.store_id == store_id)
    if event_type and not skip_type:
        c.append(RegisterEvent.event_type == event_type)
    if register_id:
        c.append(RegisterEvent.register_id == register_id)
    if cashier_id:
        c.append(RegisterEvent.cashier_id == cashier_id)
    if event_id:
        c.append(RegisterEvent.id == event_id)
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
    event_id: Annotated[str | None, Query(min_length=1, max_length=36)] = None,
    register_id: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
    cashier_id: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
    format: Literal["json", "csv"] = "json",
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
    where = conditions(
        store_id, event_type, flagged, start, end, q, register_id, cashier_id, event_id=event_id
    )
    # The type tiles ignore the selected type so they stay available as a way to change it.
    spread = conditions(
        store_id,
        event_type,
        flagged,
        start,
        end,
        q,
        register_id,
        cashier_id,
        skip_type=True,
        event_id=event_id,
    )
    if format == "csv":
        column = SORTS[sort]
        order = column.desc().nulls_last() if dir == "desc" else column.asc().nulls_last()
        query = scope.restrict(
            select(RegisterEvent).where(*where), user, RegisterEvent.store_id
        ).order_by(order, RegisterEvent.event_time.desc(), RegisterEvent.id)
        rows = (await db.execute(query.limit(CSV_MAX_ROWS + 1))).scalars().all()
        truncated = len(rows) > CSV_MAX_ROWS
        rows = rows[:CSV_MAX_ROWS]
        columns = (
            "business_date",
            "event_time",
            "store_id",
            "register_id",
            "cashier_id",
            "event_type",
            "sub_type",
            "transaction_number",
            "original_transaction_number",
            "reason_code",
            "amount",
            "currency",
            "audit_code",
        )
        return Response(
            content=csv_table(columns, ((view(row)[key] for key in columns) for row in rows)),
            media_type="text/csv",
            headers={
                "Content-Disposition": "attachment; filename=register-events.csv",
                "X-Report-Truncated": str(truncated).lower(),
            },
        )
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


CONTEXT_EACH_SIDE = 7


@router.get("/{event_id}/context")
async def register_event_context(
    event_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role(*ROLES)),
):
    """The events either side of one event on the same register and day, in the order they happened.

    A void, no-sale or cash pull means more with what came before and after it. This reads only a
    few rows around the event, so it is cheap to ask for when a row is opened, however busy the day.
    """
    event = await db.get(RegisterEvent, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Register event not found")
    scope.require_store(user, event.store_id)
    same_till = (
        RegisterEvent.store_id == event.store_id,
        RegisterEvent.business_date == event.business_date,
        RegisterEvent.register_id.is_(None)
        if event.register_id is None
        else RegisterEvent.register_id == event.register_id,
    )
    ordering = (RegisterEvent.event_time, RegisterEvent.id)
    before = (
        (
            await db.execute(
                select(RegisterEvent)
                .where(
                    *same_till,
                    (RegisterEvent.event_time < event.event_time)
                    | (
                        (RegisterEvent.event_time == event.event_time)
                        & (RegisterEvent.id < event.id)
                    ),
                )
                .order_by(RegisterEvent.event_time.desc(), RegisterEvent.id.desc())
                .limit(CONTEXT_EACH_SIDE + 1)
            )
        )
        .scalars()
        .all()
    )
    after = (
        (
            await db.execute(
                select(RegisterEvent)
                .where(
                    *same_till,
                    (RegisterEvent.event_time > event.event_time)
                    | (
                        (RegisterEvent.event_time == event.event_time)
                        & (RegisterEvent.id > event.id)
                    ),
                )
                .order_by(*ordering)
                .limit(CONTEXT_EACH_SIDE + 1)
            )
        )
        .scalars()
        .all()
    )
    return {
        "event_id": event.id,
        "store_id": event.store_id,
        "register_id": event.register_id,
        "business_date": event.business_date,
        "earlier_not_shown": len(before) > CONTEXT_EACH_SIDE,
        "later_not_shown": len(after) > CONTEXT_EACH_SIDE,
        "events": [
            view(row)
            for row in [*reversed(before[:CONTEXT_EACH_SIDE]), event, *after[:CONTEXT_EACH_SIDE]]
        ],
    }


@router.get("/{event_id}/detail-corrections")
async def list_paid_out_corrections(
    event_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role(*ROLES)),
):
    event = await db.get(RegisterEvent, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Register event not found")
    scope.require_store(user, event.store_id)
    rows = (
        await db.execute(
            select(RegisterEventAdjustment)
            .where(RegisterEventAdjustment.event_id == event.id)
            .order_by(RegisterEventAdjustment.created_at.desc(), RegisterEventAdjustment.id.desc())
            .limit(50)
        )
    ).scalars().all()
    return {"items": [adjustment_view(row) for row in rows]}


@router.get("/detail-corrections")
async def paid_out_correction_queue(
    status: str | None = None,
    q: str | None = Query(default=None, max_length=255),
    page: int = Query(default=1, ge=1, le=10000),
    page_size: int = Query(default=50, ge=1, le=200),
    format: Literal["json", "csv"] = "json",
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it")),
):
    """A global, store-scoped review queue for corrected paid-out metadata."""
    if status is not None and status not in {"Proposed", "Approved", "Rejected"}:
        raise HTTPException(status_code=422, detail="Invalid correction status")
    query = (
        select(RegisterEventAdjustment, RegisterEvent)
        .join(RegisterEvent, RegisterEventAdjustment.event_id == RegisterEvent.id)
    )
    query = scope.restrict(query, user, RegisterEvent.store_id)
    if q and q.strip():
        term = q.strip()
        query = query.where(
            or_(
                contains_literal(RegisterEvent.store_id, term),
                contains_literal(RegisterEvent.register_id, term),
                contains_literal(RegisterEvent.transaction_number, term),
                contains_literal(RegisterEventAdjustment.rationale, term),
                contains_literal(RegisterEventAdjustment.proposed_by, term),
                contains_literal(cast(RegisterEventAdjustment.proposed_values, String), term),
            )
        )
    unfiltered = query.subquery()
    status_rows = (
        await db.execute(
            select(unfiltered.c.status, func.count()).group_by(unfiltered.c.status)
        )
    ).all()
    counts = {row[0]: row[1] for row in status_rows}
    if status:
        query = query.where(RegisterEventAdjustment.status == status)
    total = sum(counts.values()) if status is None else counts.get(status, 0)
    if format == "csv":
        rows = (
            await db.execute(
                query.order_by(
                    RegisterEventAdjustment.created_at.desc(), RegisterEventAdjustment.id.desc()
                ).limit(PAID_OUT_CORRECTION_EXPORT_MAX + 1)
            )
        ).all()
        truncated = len(rows) > PAID_OUT_CORRECTION_EXPORT_MAX
        rows = rows[:PAID_OUT_CORRECTION_EXPORT_MAX]
        columns = (
            "created_at",
            "store_id",
            "business_date",
            "register_id",
            "transaction_number",
            "amount",
            "currency",
            "status",
            "proposed_by",
            "reviewed_by",
            "rationale",
            "proposed_values",
        )
        return Response(
            content=csv_table(
                columns,
                (
                    (
                        adjustment.created_at.isoformat(),
                        event.store_id,
                        event.business_date,
                        event.register_id,
                        event.transaction_number,
                        event.amount,
                        event.currency,
                        adjustment.status,
                        adjustment.proposed_by,
                        adjustment.reviewed_by,
                        adjustment.rationale,
                        json.dumps(adjustment_view(adjustment)["proposed_values"], sort_keys=True),
                    )
                    for adjustment, event in rows
                ),
            ),
            media_type="text/csv",
            headers={
                "Content-Disposition": "attachment; filename=paid-out-corrections.csv",
                "X-Report-Truncated": str(truncated).lower(),
            },
        )
    rows = (
        await db.execute(
            query.order_by(
                RegisterEventAdjustment.created_at.desc(), RegisterEventAdjustment.id.desc()
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return {
        "items": [
            {
                **adjustment_view(adjustment),
                "store_id": event.store_id,
                "business_date": event.business_date,
                "register_id": event.register_id,
                "transaction_number": event.transaction_number,
                "amount": str(event.amount) if event.amount is not None else None,
                "currency": event.currency,
            }
            for adjustment, event in rows
        ],
        "total": total,
        "page": page,
        "pages": max(1, -(-total // page_size)),
        "page_size": page_size,
        "status_counts": {
            key: counts.get(key, 0) for key in ("Proposed", "Approved", "Rejected")
        },
    }


@router.post("/{event_id}/detail-corrections")
async def propose_paid_out_correction(
    event_id: str,
    body: PaidOutCorrectionInput,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it")),
):
    if not body.rationale.strip():
        raise HTTPException(status_code=422, detail="A correction rationale is required")
    validate_paid_out_values(body.proposed_values)
    event = (
        await db.execute(
            select(RegisterEvent).where(RegisterEvent.id == event_id).with_for_update()
        )
    ).scalar_one_or_none()
    if event is None:
        raise HTTPException(status_code=404, detail="Register event not found")
    scope.require_store(user, event.store_id)
    if event.event_type != "paid_out":
        raise HTTPException(status_code=422, detail="Paid-out details can only be corrected on paid-out events")
    pending = await db.scalar(
        select(func.count(RegisterEventAdjustment.id)).where(
            RegisterEventAdjustment.event_id == event.id,
            RegisterEventAdjustment.status == "Proposed",
        )
    )
    if pending:
        raise HTTPException(status_code=409, detail="A paid-out detail correction is already awaiting review")
    current = event.details or {}
    before = {field: current.get(field) for field in body.proposed_values}
    if all(before[field] == value for field, value in body.proposed_values.items()):
        raise HTTPException(status_code=422, detail="The proposed paid-out details do not change the current values")
    row = RegisterEventAdjustment(
        id=str(uuid.uuid4()),
        event_id=event.id,
        proposed_values={"before": before, "after": body.proposed_values},
        rationale=body.rationale.strip(),
        proposed_by=user["user"] or "ontology.user",
    )
    db.add(row)
    audit_event(
        db,
        "REGISTER_EVENT_DETAIL_CORRECTION_PROPOSED",
        row,
        row.proposed_by,
        "Paid-out details correction proposed",
        after={"event_id": event.id, "values": row.proposed_values, "rationale": row.rationale},
    )
    await db.commit()
    return adjustment_view(row)


@router.post("/detail-corrections/{adjustment_id}/approve")
async def approve_paid_out_correction(
    adjustment_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance")),
):
    row = (
        await db.execute(
            select(RegisterEventAdjustment)
            .where(RegisterEventAdjustment.id == adjustment_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None or row.status != "Proposed":
        raise HTTPException(status_code=409, detail="Proposed paid-out correction not found")
    event = (
        await db.execute(
            select(RegisterEvent).where(RegisterEvent.id == row.event_id).with_for_update()
        )
    ).scalar_one_or_none()
    if event is None or event.event_type != "paid_out":
        raise HTTPException(status_code=409, detail="The paid-out event is no longer available")
    scope.require_store(user, event.store_id)
    before = row.proposed_values.get("before", {})
    after = row.proposed_values.get("after", {})
    current = event.details or {}
    if any(current.get(field) != value for field, value in before.items()):
        raise HTTPException(
            status_code=409,
            detail="Paid-out details changed after the correction was proposed; propose it again",
        )
    mask_character = await credit_card_number_mask(db)
    updated = dict(current)
    for field, value in after.items():
        if value is None:
            updated.pop(field, None)
        else:
            updated[field] = redact_sensitive_fields({field: value}, mask_character)[field]
    event.details = updated
    row.status = "Approved"
    row.reviewed_by = user["user"] or "ontology.user"
    row.reviewed_at = utcnow()
    audit_event(
        db,
        "REGISTER_EVENT_DETAIL_CORRECTION_APPROVED",
        row,
        row.reviewed_by,
        "Paid-out details correction approved",
        before={"event_id": event.id, "values": before},
        after={"event_id": event.id, "values": after},
    )
    await db.commit()
    return adjustment_view(row)


@router.post("/detail-corrections/{adjustment_id}/reject")
async def reject_paid_out_correction(
    adjustment_id: str,
    body: CorrectionRejectionInput,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance")),
):
    if not body.reason.strip():
        raise HTTPException(status_code=422, detail="A rejection reason is required")
    row = (
        await db.execute(
            select(RegisterEventAdjustment)
            .where(RegisterEventAdjustment.id == adjustment_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None or row.status != "Proposed":
        raise HTTPException(status_code=409, detail="Proposed paid-out correction not found")
    event = await db.get(RegisterEvent, row.event_id)
    if event is None:
        raise HTTPException(status_code=409, detail="The paid-out event is no longer available")
    scope.require_store(user, event.store_id)
    row.status = "Rejected"
    row.reviewed_by = user["user"] or "ontology.user"
    row.reviewed_at = utcnow()
    audit_event(
        db,
        "REGISTER_EVENT_DETAIL_CORRECTION_REJECTED",
        row,
        row.reviewed_by,
        "Paid-out details correction rejected",
        after={"event_id": event.id, "reason": body.reason.strip()},
    )
    await db.commit()
    return adjustment_view(row)
