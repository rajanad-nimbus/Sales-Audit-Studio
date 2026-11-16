"""Transaction comments, retained in the append-only audit stream."""

import uuid
from typing import Annotated

from database import get_db
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.audit.models import AuditEvent
from apps.cases.lineage import case_creation_mentions_transaction
from apps.cases.models import Case
from apps.cases.models import Exception as ExceptionRow
from apps.security import scope
from apps.security.authentication import require_role
from apps.transactions.models import CanonicalTransaction

router = APIRouter(tags=["Transactions"])


class CommentInput(BaseModel):
    text: str = Field(min_length=1, max_length=2000)

    @field_validator("text")
    @classmethod
    def trim_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Comment must not be blank")
        return value


async def checked_transaction(db: AsyncSession, transaction_id: str, user: dict):
    tx = await db.get(CanonicalTransaction, transaction_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")
    scope.require_store(user, tx.store_id)
    return tx


def comment_view(event: AuditEvent) -> dict:
    return {
        "id": event.id,
        "text": event.after_state["text"],
        "actor": event.actor,
        "created_at": event.created_at,
    }


@router.get("/{transaction_id}/comments")
async def list_comments(
    transaction_id: str,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("analyst", "finance", "it", "auditor")),
):
    await checked_transaction(db, transaction_id, user)
    rows = (
        (
            await db.execute(
                select(AuditEvent)
                .where(
                    AuditEvent.object_type == "Transaction",
                    AuditEvent.object_id == transaction_id,
                    AuditEvent.event_type == "TRANSACTION_COMMENT_ADDED",
                )
                .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
                .offset(offset)
                .limit(101)
            )
        )
        .scalars()
        .all()
    )
    return {"items": [comment_view(row) for row in rows[:100]], "has_more": len(rows) > 100}


@router.get("/{transaction_id}/errors")
async def transaction_errors(
    transaction_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("analyst", "finance", "it", "auditor")),
):
    """Show audited exceptions whose case creation event names this transaction as evidence."""
    transaction = await checked_transaction(db, transaction_id, user)
    created = AuditEvent
    linked = case_creation_mentions_transaction(db, transaction_id, created)
    rows = (
        await db.execute(
            select(Case, ExceptionRow)
            .select_from(created)
            .join(Case, Case.id == created.case_id)
            .join(ExceptionRow, ExceptionRow.case_id == Case.id)
            .where(
                created.event_type == "CASE_CREATED",
                linked,
                Case.store_id == transaction.store_id,
            )
            .order_by(Case.created_at.desc(), Case.id, ExceptionRow.id)
            .limit(101)
        )
    ).all()
    return {
        "items": [
            {
                "id": error.id,
                "code": error.exception_type,
                "severity": error.severity,
                "status": error.status,
                "case_id": case.id,
                "case_number": case.case_number,
                "case_status": case.status,
                "business_date": case.business_date,
            }
            for case, error in rows[:100]
        ],
        "has_more": len(rows) > 100,
    }


@router.post("/{transaction_id}/comments", status_code=201)
async def add_comment(
    transaction_id: str,
    body: CommentInput,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("analyst", "finance")),
):
    await checked_transaction(db, transaction_id, user)
    event = AuditEvent(
        id=str(uuid.uuid4()),
        event_type="TRANSACTION_COMMENT_ADDED",
        actor=user.get("user") or "ontology.user",
        object_type="Transaction",
        object_id=transaction_id,
        action_description="Transaction comment added",
        after_state={"text": body.text},
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return comment_view(event)
