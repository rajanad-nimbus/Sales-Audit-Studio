"""Versioned, preview-first exchange of variance tolerance rules between environments.

Applying a package makes the active tolerance set equal to the package: rules are created, changed or
retired by their scope (total name and store group), and the whole change is one audited transaction.
"""

import hashlib
import json
import uuid
from decimal import Decimal
from typing import Literal

from database import get_db
from fastapi import APIRouter, Depends, HTTPException
from integrations.ontology.nimbus import audit
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.security.authentication import require_role
from apps.store_days.freshness import lock_configuration
from apps.store_days.models import VarianceTolerance
from apps.store_days.variance_routes import ToleranceBody

router = APIRouter(prefix="/api/variance-tolerances/package", tags=["Store days"])
MAX_RULES = 500
Scope = tuple[str | None, str | None]


class PackagedTolerance(ToleranceBody):
    model_config = ConfigDict(extra="forbid")


class TolerancePackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["zetsa.variance-tolerances"]
    version: Literal[1]
    tolerances: list[PackagedTolerance] = Field(max_length=MAX_RULES)

    @model_validator(mode="after")
    def scopes_are_unique(self):
        scopes = [(rule.total_name, rule.location_trait) for rule in self.tolerances]
        if len(set(scopes)) != len(scopes):
            raise ValueError("Each total and store group may appear only once")
        return self


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package: TolerancePackage


class ApplyRequest(PreviewRequest):
    preview_token: str = Field(pattern=r"^[a-f0-9]{64}$")
    reason: str = Field(min_length=10, max_length=2000)

    @field_validator("reason")
    @classmethod
    def valid_reason(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 10:
            raise ValueError("Reason must contain at least 10 non-whitespace characters")
        return normalized


def _amounts(row) -> dict:
    return {
        "within_amount": f"{Decimal(row.within_amount):.2f}",
        "case_amount": f"{Decimal(row.case_amount):.2f}",
    }


def _entry(scope: Scope, amounts: dict) -> dict:
    return {"total_name": scope[0], "location_trait": scope[1], **amounts}


async def _active(db: AsyncSession, *, lock: bool = False) -> dict[Scope, VarianceTolerance]:
    query = select(VarianceTolerance).where(VarianceTolerance.active.is_(True))
    if lock:
        query = query.with_for_update()
    rows = (await db.execute(query)).scalars().all()
    return {(row.total_name, row.location_trait): row for row in rows}


def _sorted(entries: list[dict]) -> list[dict]:
    return sorted(entries, key=lambda e: (e["total_name"] or "", e["location_trait"] or ""))


def _plan(package: TolerancePackage, current: dict[Scope, VarianceTolerance]) -> dict:
    wanted = {(rule.total_name, rule.location_trait): _amounts(rule) for rule in package.tolerances}
    existing = {scope: _amounts(row) for scope, row in current.items()}
    created = [_entry(s, a) for s, a in wanted.items() if s not in existing]
    updated = [
        {**_entry(s, a), "current": existing[s]}
        for s, a in wanted.items()
        if s in existing and existing[s] != a
    ]
    retired = [_entry(s, a) for s, a in existing.items() if s not in wanted]
    unchanged = sum(1 for s, a in wanted.items() if existing.get(s) == a)
    canonical = json.dumps(
        {
            "package": sorted(map(json.dumps, [_entry(s, a) for s, a in wanted.items()])),
            "current": sorted(map(json.dumps, [_entry(s, a) for s, a in existing.items()])),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "valid": True,
        "preview_token": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "changed": bool(created or updated or retired),
        "created": _sorted(created),
        "updated": _sorted(updated),
        "retired": _sorted(retired),
        "unchanged": unchanged,
    }


@router.get("")
async def export_tolerance_package(
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("finance", "it", "auditor")),
):
    current = await _active(db)
    return {
        "format": "zetsa.variance-tolerances",
        "version": 1,
        "tolerances": _sorted([_entry(s, _amounts(row)) for s, row in current.items()]),
    }


@router.post("/preview")
async def preview_tolerance_package(
    body: PreviewRequest,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("finance", "it")),
):
    return _plan(body.package, await _active(db))


@router.post("/apply")
async def apply_tolerance_package(
    body: ApplyRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance")),
):
    await lock_configuration(db, exclusive=True)
    current = await _active(db, lock=True)
    plan = _plan(body.package, current)
    if plan["preview_token"] != body.preview_token:
        raise HTTPException(
            status_code=409,
            detail="Variance tolerances changed after preview. Preview the package again.",
        )
    if not plan["changed"]:
        return {"changed": False}
    actor = user.get("user") or "ontology.user"
    wanted = {(r.total_name, r.location_trait): r for r in body.package.tolerances}
    for scope, row in current.items():
        if scope not in wanted:
            row.active, row.updated_by = False, actor
        else:
            row.within_amount, row.case_amount = (
                wanted[scope].within_amount,
                wanted[scope].case_amount,
            )
            row.updated_by = actor
    for scope, rule in wanted.items():
        if scope not in current:
            db.add(VarianceTolerance(id=str(uuid.uuid4()), **rule.model_dump(), updated_by=actor))
    audit(
        db,
        "VARIANCE_TOLERANCE_PACKAGE_APPLIED",
        None,
        "VarianceTolerancePackage",
        "variance-tolerances",
        f"Variance tolerance package applied: {body.reason}",
        actor=actor,
        after={
            "created": len(plan["created"]),
            "updated": len(plan["updated"]),
            "retired": len(plan["retired"]),
        },
    )
    await db.commit()
    return {
        "changed": True,
        "created": len(plan["created"]),
        "updated": len(plan["updated"]),
        "retired": len(plan["retired"]),
    }
