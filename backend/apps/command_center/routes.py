"""Command Center data."""

from database import get_db
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from apps.command_center.plan import operating_plan
from apps.security.authentication import require_role

router = APIRouter(prefix="/api/command-center", tags=["Command Center"])


@router.get("/plan")
async def plan(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("analyst", "finance", "it", "auditor")),
):
    """Where today's work stands: is the data in, and can the store days be balanced."""
    return await operating_plan(db, user)
