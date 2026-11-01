"""Shared case access rules and locking."""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.cases.models import Case
from apps.security import scope


async def locked_scoped_case(db: AsyncSession, case_id: str, user: dict) -> Case:
    """Load and lock a case, enforcing store scope before any mutation."""
    case = (
        await db.execute(
            select(Case)
            .where(Case.id == case_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    scope.require_store(user, case.store_id)
    return case
