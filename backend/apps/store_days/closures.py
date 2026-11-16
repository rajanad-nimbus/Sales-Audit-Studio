"""Store-day closure request and approval endpoints."""

import uuid

from database import get_db
from fastapi import APIRouter, Depends, HTTPException
from integrations.ontology.nimbus import audit
from model_base import utcnow
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.cases.models import Case
from apps.ingest import reconciliation
from apps.security import scope
from apps.security.authentication import require_role
from apps.store_days import variances
from apps.store_days.models import AuditTotal, StoreDay, StoreDayClosureRequest
from apps.store_days.periods import reopen_guards
from apps.store_days.readiness import CLEAN, COMPLETE, readiness
from apps.store_days.service import (
    OPTIONAL_COMPARISON_TOTALS,
    REQUIRED_COMPARISON_TOTALS,
    comparison_blockers,
    get_day,
    store_day_view,
)
from apps.system.options import store_days_worked_in_order

closure_router = APIRouter(prefix="/api/store-days", tags=["Store days"])


class ReopenRequest(BaseModel):
    rationale: str = Field(min_length=1, max_length=2000)


class ClosureRequestBody(BaseModel):
    evidence_basis: str = Field(min_length=1, max_length=5000)


class ClosureReviewBody(BaseModel):
    rationale: str = Field(min_length=1, max_length=2000)


class AuditSignoffBody(BaseModel):
    rationale: str = Field(min_length=10, max_length=2000)

    @field_validator("rationale")
    @classmethod
    def require_meaningful_rationale(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 10:
            raise ValueError("Auditor sign-off rationale must be at least 10 characters")
        return value


@closure_router.post("/{store_id}/{business_date}/audit-signoff")
async def sign_off_store_day_audit(
    store_id: str,
    business_date: str,
    body: AuditSignoffBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("analyst")),
):
    scope.require_store(user, store_id)
    await reconciliation.lock_reconciliation(db)
    day = await get_day(db, store_id, business_date, lock=True)
    if day.status == "Closed":
        raise HTTPException(status_code=409, detail="A closed Store Day cannot be signed off")
    view = (await readiness(db, [day]))[day.id]
    if view["ingest"]["state"] != COMPLETE or view["audit"]["state"] != CLEAN:
        raise HTTPException(
            status_code=422,
            detail="Auditor sign-off requires complete data and clean current totals",
        )
    if view["audit"]["signed_off"]:
        raise HTTPException(status_code=409, detail="Current totals already have auditor sign-off")
    actor = user["user"] or "ontology.user"
    day.audit_signed_version = day.audit_version
    day.audit_signed_by = actor
    day.audit_signed_at = utcnow()
    day.audit_signoff_rationale = body.rationale
    audit(
        db,
        "STORE_DAY_AUDIT_SIGNED_OFF",
        None,
        "StoreDay",
        day.id,
        f"Totals version {day.audit_version} signed off: {body.rationale}",
        actor=actor,
    )
    await db.commit()
    return {
        "signed_off": True,
        "signed_version": day.audit_signed_version,
        "signed_by": day.audit_signed_by,
        "signed_at": day.audit_signed_at,
        "signoff_rationale": day.audit_signoff_rationale,
    }


@closure_router.post("/{store_id}/{business_date}/closure-requests")
async def request_store_day_closure(
    store_id: str,
    business_date: str,
    body: ClosureRequestBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it")),
):
    scope.require_store(user, store_id)
    if not body.evidence_basis.strip():
        raise HTTPException(status_code=422, detail="Closure evidence basis is required")
    await reconciliation.lock_reconciliation(db)
    day = await get_day(db, store_id, business_date, lock=True)
    if day.status == "Closed":
        raise HTTPException(status_code=409, detail="Store day is already closed")
    if await store_days_worked_in_order(db):
        earlier_open_day = (
            await db.execute(
                select(StoreDay.business_date, StoreDay.status)
                .where(
                    StoreDay.store_id == store_id,
                    StoreDay.business_date < business_date,
                    StoreDay.status != "Closed",
                )
                .order_by(StoreDay.business_date)
                .limit(1)
            )
        ).first()
        if earlier_open_day:
            earlier_date, earlier_status = earlier_open_day
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Cannot close {business_date}: earlier store day {earlier_date} "
                    f"is still {earlier_status}. Close store days in date order."
                ),
            )
    current_readiness = (await readiness(db, [day]))[day.id]
    if not current_readiness["audit"]["signed_off"]:
        raise HTTPException(
            status_code=422,
            detail="Cannot request closure: auditor sign-off is required for current totals",
        )
    pending = (
        (
            await db.execute(
                select(StoreDayClosureRequest.id).where(
                    StoreDayClosureRequest.store_day_id == day.id,
                    StoreDayClosureRequest.status == "Pending",
                )
            )
        )
        .scalars()
        .first()
    )
    if pending:
        raise HTTPException(
            status_code=409, detail="A closure request is already pending Finance review"
        )
    request = StoreDayClosureRequest(
        id=str(uuid.uuid4()),
        store_day_id=day.id,
        requested_by=user["user"] or "ontology.user",
        evidence_basis=body.evidence_basis.strip(),
    )
    db.add(request)
    audit(
        db,
        "STORE_DAY_CLOSURE_REQUESTED",
        None,
        "StoreDay",
        day.id,
        "Store day closure requested",
        actor=request.requested_by,
    )
    await db.commit()
    return {"id": request.id, "status": request.status}


@closure_router.post("/{store_id}/{business_date}/closure-requests/{request_id}/reject")
async def reject_store_day_closure(
    store_id: str,
    business_date: str,
    request_id: str,
    body: ClosureReviewBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance")),
):
    scope.require_store(user, store_id)
    if not body.rationale.strip():
        raise HTTPException(status_code=422, detail="A rejection rationale is required")
    day = await get_day(db, store_id, business_date, lock=True)
    request = (
        await db.execute(
            select(StoreDayClosureRequest).where(
                StoreDayClosureRequest.id == request_id,
                StoreDayClosureRequest.store_day_id == day.id,
            )
        )
    ).scalar_one_or_none()
    if not request or request.status != "Pending":
        raise HTTPException(status_code=409, detail="Pending closure request not found")
    request.status, request.reviewed_by, request.reviewed_at, request.review_reason = (
        "Rejected",
        user["user"] or "ontology.user",
        utcnow(),
        body.rationale.strip(),
    )
    audit(
        db,
        "STORE_DAY_CLOSURE_REJECTED",
        None,
        "StoreDay",
        day.id,
        f"Closure request rejected: {request.review_reason}",
        actor=request.reviewed_by,
    )
    await db.commit()
    return {"id": request.id, "status": request.status}


@closure_router.post("/{store_id}/{business_date}/close")
async def close_store_day(
    store_id: str,
    business_date: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance")),
):
    scope.require_store(user, store_id)
    await reconciliation.lock_reconciliation(db)
    day = await get_day(db, store_id, business_date, lock=True)
    if day.status == "Closed":
        raise HTTPException(status_code=409, detail="Store day is already closed")
    totals = (
        (
            await db.execute(
                select(AuditTotal).where(
                    AuditTotal.store_day_id == day.id,
                    AuditTotal.audit_version == day.audit_version,
                    AuditTotal.total_name.in_(
                        REQUIRED_COMPARISON_TOTALS | OPTIONAL_COMPARISON_TOTALS
                    ),
                    AuditTotal.level == "Store",
                )
            )
        )
        .scalars()
        .all()
    )
    evaluations = await variances.evaluate(db, day, list(totals))
    blockers = comparison_blockers(day, totals, set(variances.unresolved(evaluations)))
    if "totals are stale; re-total the store day" in blockers:
        raise HTTPException(
            status_code=422, detail="Cannot close: totals are stale; re-total the store day"
        )
    if "current reconciliation totals are missing" in blockers:
        raise HTTPException(
            status_code=422, detail="Cannot close: current reconciliation totals are missing"
        )
    open_case_count = (
        await db.execute(
            select(func.count())
            .select_from(Case)
            .where(
                Case.store_id == store_id,
                Case.business_date == business_date,
                Case.status.not_in(("Closed",)),
            )
        )
    ).scalar_one()
    if open_case_count:
        raise HTTPException(
            status_code=422, detail=f"Cannot close: {open_case_count} case(s) remain open"
        )
    if "unresolved reconciliation variance" in blockers:
        raise HTTPException(
            status_code=422,
            detail=(
                "Cannot close: a reconciliation variance is outside tolerance and has no "
                "explanation; give a reason, or link the case or correction behind it"
            ),
        )
    # Sign-off is only meaningful once totals are current and nothing else blocks closure.
    current_readiness = (await readiness(db, [day]))[day.id]
    if not current_readiness["audit"]["signed_off"]:
        raise HTTPException(
            status_code=422,
            detail="Cannot close: auditor sign-off is required for current totals",
        )
    request = (
        (
            await db.execute(
                select(StoreDayClosureRequest)
                .where(
                    StoreDayClosureRequest.store_day_id == day.id,
                    StoreDayClosureRequest.status == "Pending",
                )
                .order_by(StoreDayClosureRequest.created_at.desc())
            )
        )
        .scalars()
        .first()
    )
    if not request:
        raise HTTPException(
            status_code=422, detail="A pending closure request with evidence is required"
        )
    actor = user["user"] or "ontology.user"
    if actor == request.requested_by:
        raise HTTPException(
            status_code=403,
            detail="A different user from the one who requested closure must approve it",
        )
    request.status, request.reviewed_by, request.reviewed_at = "Approved", actor, utcnow()
    day.status, day.closed_at, day.closed_by = "Closed", utcnow(), actor
    audit(
        db,
        "STORE_DAY_CLOSED",
        None,
        "StoreDay",
        day.id,
        f"Store day {store_id}/{business_date} closed",
        actor=actor,
    )
    await db.commit()
    await db.refresh(day)
    return store_day_view(day)


@closure_router.post("/{store_id}/{business_date}/reopen")
async def reopen_store_day(
    store_id: str,
    business_date: str,
    body: ReopenRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance")),
):
    scope.require_store(user, store_id)
    if not body.rationale.strip():
        raise HTTPException(status_code=422, detail="A reopen rationale is required")
    day = await get_day(db, store_id, business_date, lock=True)
    if day.status != "Closed":
        raise HTTPException(status_code=409, detail="Only a closed store day can be reopened")
    guards = await reopen_guards(db, day)
    if guards:
        raise HTTPException(status_code=409, detail=f"Cannot reopen: {'; '.join(guards)}")
    closed_at, closed_by = day.closed_at, day.closed_by
    day.status, day.closed_at, day.reopened_reason, day.reopened_by = (
        "Reopened",
        None,
        body.rationale.strip(),
        user["user"] or "ontology.user",
    )
    audit(
        db,
        "STORE_DAY_REOPENED",
        None,
        "StoreDay",
        day.id,
        f"Store day reopened: {body.rationale.strip()}",
        actor=user["user"] or "ontology.user",
        # The reopen history is read back from these events.
        after={
            "reason": body.rationale.strip(),
            "audit_version": day.audit_version,
            "closed_at": closed_at.isoformat() if closed_at else None,
            "closed_by": closed_by,
        },
    )
    await db.commit()
    await db.refresh(day)
    return store_day_view(day)
