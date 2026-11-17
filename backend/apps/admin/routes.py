"""Admin configuration: switch pages and whole modules on or off for every non-admin role."""

import time
import uuid

from database import async_session, get_db
from fastapi import APIRouter, Depends
from integrations.ontology.nimbus import audit
from model_base import utcnow
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.security import access
from apps.security.authentication import require_role
from apps.system.models import SystemOption

router = APIRouter()

PREFIX = "hidden_screen:"
REFRESH_SECONDS = 5.0
_loaded_at = 0.0

# A module is a named group of screens that is switched on or off together.
MODULES: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    (
        "workspace",
        "Workspace",
        "Guided tasks, Action Center and Cases.",
        ("guided-tasks", "actions", "cases"),
    ),
    (
        "cash",
        "Cash office and ACH",
        "Cash office deposits and ACH maintenance.",
        ("cash-office", "ach-maintenance"),
    ),
    (
        "reports",
        "Reports",
        "Every report under Reports.",
        tuple(s.key for s in access.SCREENS if s.group == "Reports"),
    ),
    (
        "diagnostics",
        "Diagnostics",
        "System health, feeds, integrity and activity.",
        tuple(s.key for s in access.SCREENS if s.group == "Diagnostics"),
    ),
    (
        "corrections",
        "Investigate and correct",
        "Audit errors, transaction corrections and the audit trail.",
        tuple(s.key for s in access.SCREENS if s.group == "Investigate & Correct"),
    ),
    (
        "integrations",
        "Imports and exports",
        "Source imports and export deliveries.",
        ("imports", "exports"),
    ),
)


async def refresh_hidden_screens(force: bool = False) -> None:
    """Reload the hidden-screen list from the database, at most every few seconds per process."""
    global _loaded_at
    now = time.monotonic()
    if not force and now - _loaded_at < REFRESH_SECONDS:
        return
    async with async_session() as db:
        keys = (
            (await db.execute(select(SystemOption.key).where(SystemOption.key.like(PREFIX + "%"))))
            .scalars()
            .all()
        )
    access.set_hidden_screens(k[len(PREFIX) :] for k in keys)
    _loaded_at = now


class ScreenVisibilityIn(BaseModel):
    hidden: list[str] = Field(default_factory=list, max_length=200)
    reason: str = Field(min_length=10, max_length=2000)


def _view() -> dict:
    hidden = access.HIDDEN_SCREENS
    return {
        "screens": [
            {
                "key": s.key,
                "path": s.path,
                "label": s.label,
                "group": s.group,
                "hidden": s.key in hidden,
                "protected": s.key in access.PROTECTED_SCREENS,
            }
            for s in access.SCREENS
        ],
        "modules": [
            {"key": k, "label": label, "description": text, "screens": list(keys)}
            for k, label, text, keys in MODULES
        ],
    }


@router.get("/api/admin/config")
async def get_admin_config(_: dict = Depends(require_role())):
    await refresh_hidden_screens(force=True)
    return _view()


@router.put("/api/admin/config/screens")
async def update_screen_visibility(
    body: ScreenVisibilityIn,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role()),
):
    allowed = {s.key for s in access.SCREENS} - access.PROTECTED_SCREENS
    wanted = sorted(set(body.hidden) & allowed)
    actor = user["user"] or "ontology.user"
    before = sorted(access.HIDDEN_SCREENS)
    await db.execute(delete(SystemOption).where(SystemOption.key.like(PREFIX + "%")))
    now = utcnow()
    for key in wanted:
        db.add(
            SystemOption(key=PREFIX + key, value="true", updated_by=actor, updated_at=now)
        )
    audit(
        db,
        "SCREEN_VISIBILITY_CHANGED",
        None,
        "SystemOption",
        str(uuid.uuid5(uuid.NAMESPACE_URL, "screen_visibility")),
        f"Hidden screens changed from {len(before)} to {len(wanted)}: {body.reason.strip()}",
        actor=actor,
        before={"hidden": before},
        after={"hidden": wanted},
    )
    await db.commit()
    await refresh_hidden_screens(force=True)
    return _view()
