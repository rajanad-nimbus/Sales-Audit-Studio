"""Diagnostics for monitoring and auditability: system health, feed health, and audit integrity."""

from typing import Annotated

from database import get_db
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from apps.diagnostics import activity, integrity
from apps.diagnostics.feeds import feed_status
from apps.diagnostics.health import system_health
from apps.security import scope
from apps.security.authentication import require_role

router = APIRouter(prefix="/api/diagnostics", tags=["Diagnostics"])
ROLES = ("finance", "it", "auditor")


def system_wide(user: dict = Depends(require_role(*ROLES))) -> dict:
    """Diagnostics describe the whole system, so a user limited to some stores does not get them."""
    if scope.scoped(user):
        raise HTTPException(
            status_code=403,
            detail="Diagnostics cover every store; they need a user who can see all stores",
        )
    return user


@router.get("/health")
async def health(db: AsyncSession = Depends(get_db), _: dict = Depends(system_wide)):
    return await system_health(db)


@router.get("/feeds")
async def feeds(db: AsyncSession = Depends(get_db), _: dict = Depends(system_wide)):
    return await feed_status(db)


@router.get("/integrity")
async def audit_integrity(
    from_seq: Annotated[int | None, Query(ge=0)] = None,
    size: Annotated[int, Query(ge=100, le=integrity.MAX_WINDOW)] = integrity.DEFAULT_WINDOW,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(system_wide),
):
    """Verify one window of the audit hash chain; follow ``next_seq`` to verify the rest."""
    return await integrity.verify_window(db, from_seq, size)


@router.get("/activity")
async def audit_activity(
    events: Annotated[int, Query(ge=100, le=activity.MAX_WINDOW)] = activity.DEFAULT_WINDOW,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(system_wide),
):
    """Audit activity over the newest ``events``: by person, by kind, and the changes that matter."""
    return await activity.audit_activity(db, events)
