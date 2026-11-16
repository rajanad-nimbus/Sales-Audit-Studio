"""Audited dispositions for register sequence gaps."""

from database import get_db
from fastapi import APIRouter, Depends, HTTPException
from integrations.ontology.nimbus import audit
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.audit.models import AuditEvent
from apps.ingest.sequence import find_gaps
from apps.security import scope
from apps.security.authentication import require_role
from apps.store_days.service import get_day

router = APIRouter(prefix="/api/store-days", tags=["Missing transactions"])


class SequenceGapDecision(BaseModel):
    decision: str = Field(pattern="^(dismiss|restore)$")
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("reason")
    @classmethod
    def reason_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("A reason is required")
        return value


@router.post("/{store_id}/{business_date}/sequence-gaps/{register_id}/{transaction_number}")
async def decide_sequence_gap(
    store_id: str,
    business_date: str,
    register_id: str,
    transaction_number: int,
    body: SequenceGapDecision,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("analyst", "finance", "it")),
):
    scope.require_store(user, store_id)
    day = await get_day(db, store_id, business_date, lock=True)
    if transaction_number < 0 or transaction_number > 2_147_483_647:
        raise HTTPException(status_code=422, detail="Transaction number is out of range")
    current = (await find_gaps(db, business_date, store_id=store_id)).get(
        (store_id, business_date), {}
    )
    if transaction_number not in current.get(register_id, []):
        raise HTTPException(status_code=409, detail="This transaction number is no longer a gap")

    events = (
        (
            await db.execute(
                select(AuditEvent)
                .where(
                    AuditEvent.object_type == "RegisterSequenceGap",
                    AuditEvent.object_id == day.id,
                    AuditEvent.event_type.in_(
                        ["REGISTER_SEQUENCE_GAP_DISMISSED", "REGISTER_SEQUENCE_GAP_RESTORED"]
                    ),
                )
                .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
            )
        )
        .scalars()
        .all()
    )
    latest = next(
        (
            event
            for event in events
            if (event.after_state or {}).get("register_id") == register_id
            and (event.after_state or {}).get("transaction_number") == transaction_number
        ),
        None,
    )
    is_dismissed = latest is not None and latest.event_type == "REGISTER_SEQUENCE_GAP_DISMISSED"
    if body.decision == "dismiss" and is_dismissed:
        raise HTTPException(status_code=409, detail="This gap is already marked not a transaction")
    if body.decision == "restore" and not is_dismissed:
        raise HTTPException(status_code=409, detail="This sequence gap is not dismissed")

    event_type = (
        "REGISTER_SEQUENCE_GAP_DISMISSED"
        if body.decision == "dismiss"
        else "REGISTER_SEQUENCE_GAP_RESTORED"
    )
    audit(
        db,
        event_type,
        None,
        "RegisterSequenceGap",
        day.id,
        f"Register {register_id} transaction number {transaction_number}: {body.decision}",
        actor=user.get("user") or "ontology.user",
        after={
            "register_id": register_id,
            "transaction_number": transaction_number,
            "status": "Dismissed" if body.decision == "dismiss" else "Missing",
            "reason": body.reason,
        },
    )
    await db.commit()
    return {
        "store_id": store_id,
        "business_date": business_date,
        "register_id": register_id,
        "transaction_number": transaction_number,
        "status": "Dismissed" if body.decision == "dismiss" else "Missing",
    }
