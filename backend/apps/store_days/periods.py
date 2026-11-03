"""Accounting period close, and the checks that stop a store day reopening behind it."""

import uuid
from datetime import UTC, datetime

from database import get_db
from fastapi import APIRouter, Depends, HTTPException
from integrations.ontology.nimbus import audit
from model_base import utcnow
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.ingest.manifests import validate_business_date
from apps.security import scope
from apps.security.authentication import require_role
from apps.store_days.models import PeriodClose, StoreDay
from apps.store_days.readiness import FINAL, export_states

router = APIRouter(prefix="/api/periods", tags=["Store days"])
OPEN_DAY_SAMPLE = 10


class PeriodCloseBody(BaseModel):
    period_end: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    reason: str | None = Field(default=None, max_length=2000)

    @field_validator("period_end")
    @classmethod
    def valid_date(cls, value: str) -> str:
        return validate_business_date(value)


class PeriodReopenBody(BaseModel):
    rationale: str = Field(min_length=1, max_length=2000)

    @field_validator("rationale")
    @classmethod
    def required(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A reason is required")
        return value.strip()


def period_view(row: PeriodClose) -> dict:
    return {
        "id": row.id,
        "period_end": row.period_end,
        "status": row.status,
        "reason": row.reason,
        "closed_by": row.closed_by,
        "closed_at": row.closed_at,
        "reopened_by": row.reopened_by,
        "reopened_reason": row.reopened_reason,
        "reopened_at": row.reopened_at,
    }


async def latest_close(db: AsyncSession) -> PeriodClose | None:
    return (
        (
            await db.execute(
                select(PeriodClose)
                .where(PeriodClose.status == "Closed")
                .order_by(PeriodClose.period_end.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )


async def reopen_guards(db: AsyncSession, day: StoreDay) -> list[str]:
    """Why this closed store day may not be reopened; empty when it may."""
    reasons = []
    period = await latest_close(db)
    if period is not None and day.business_date <= period.period_end:
        reasons.append(
            f"The accounting period is closed through {period.period_end}; "
            "Finance must reopen the period first"
        )
    export = (await export_states(db, [(day.store_id, day.business_date)])).get(
        (day.store_id, day.business_date)
    )
    if export and export["state"] == FINAL:
        reasons.append(
            f"Its exports are final: {export['acknowledged']} record(s) acknowledged by the "
            "destination. Post a correction to a later day instead"
        )
    return reasons


@router.get("")
async def list_periods(
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("finance", "it", "auditor")),
):
    rows = (
        (await db.execute(select(PeriodClose).order_by(PeriodClose.closed_at.desc())))
        .scalars()
        .all()
    )
    current = await latest_close(db)
    return {
        "closed_through": current.period_end if current else None,
        "periods": [period_view(row) for row in rows],
    }


@router.post("")
async def close_period(
    body: PeriodCloseBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance")),
):
    """Close the books through a date; every store day on or before it must be closed."""
    if scope.scoped(user):
        raise HTTPException(
            status_code=403, detail="Closing a period affects every store; it needs full access"
        )
    if body.period_end >= datetime.now(UTC).date().isoformat():
        raise HTTPException(status_code=422, detail="A period can only close once it has ended")
    current = await latest_close(db)
    if current is not None and body.period_end <= current.period_end:
        raise HTTPException(
            status_code=409, detail=f"The period is already closed through {current.period_end}"
        )
    open_days = (
        await db.execute(
            select(StoreDay.store_id, StoreDay.business_date)
            .where(StoreDay.business_date <= body.period_end, StoreDay.status != "Closed")
            .order_by(StoreDay.business_date, StoreDay.store_id)
            .limit(OPEN_DAY_SAMPLE)
        )
    ).all()
    if open_days:
        total = (
            await db.execute(
                select(func.count()).where(
                    StoreDay.business_date <= body.period_end, StoreDay.status != "Closed"
                )
            )
        ).scalar_one()
        sample = ", ".join(f"{store} {day}" for store, day in open_days)
        raise HTTPException(
            status_code=422,
            detail=f"Close every store day in the period first: {total} still open ({sample})",
        )
    actor = user["user"] or "ontology.user"
    row = PeriodClose(
        id=str(uuid.uuid4()),
        period_end=body.period_end,
        status="Closed",
        reason=(body.reason or "").strip() or None,
        closed_by=actor,
    )
    db.add(row)
    audit(
        db,
        "PERIOD_CLOSED",
        None,
        "PeriodClose",
        row.id,
        f"Accounting period closed through {row.period_end}",
        actor=actor,
        after={"period_end": row.period_end, "reason": row.reason},
    )
    await db.commit()
    return period_view(row)


@router.post("/{period_id}/reopen")
async def reopen_period(
    period_id: str,
    body: PeriodReopenBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance")),
):
    """Reopen the most recent period close, so its store days can be reopened again."""
    if scope.scoped(user):
        raise HTTPException(
            status_code=403, detail="Reopening a period affects every store; it needs full access"
        )
    current = await latest_close(db)
    if current is None or current.id != period_id:
        raise HTTPException(
            status_code=409, detail="Only the most recent closed period can be reopened"
        )
    actor = user["user"] or "ontology.user"
    current.status, current.reopened_by = "Reopened", actor
    current.reopened_reason, current.reopened_at = body.rationale, utcnow()
    audit(
        db,
        "PERIOD_REOPENED",
        None,
        "PeriodClose",
        current.id,
        f"Accounting period through {current.period_end} reopened: {body.rationale}",
        actor=actor,
        after={"period_end": current.period_end, "reason": body.rationale},
    )
    await db.commit()
    return period_view(current)
