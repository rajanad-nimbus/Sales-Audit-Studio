"""Versioned, preview-first exchange of tender types between environments.

A package creates tenders and updates the ones it names. Tenders it does not name are left alone,
because a tender is deactivated, never deleted. Every row is checked exactly as the tender form checks it.
"""

import hashlib
import json
import uuid
from typing import Literal

from database import get_db
from fastapi import APIRouter, Depends, HTTPException
from integrations.ontology.nimbus import audit
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.controls.foundation.models import FoundationReference
from apps.controls.foundation.tenders import (
    TenderBody,
    audit_state,
    require_known_code,
    view,
)
from apps.security.authentication import require_role

router = APIRouter(prefix="/api/tender-types/package", tags=["Tender types"])
MAX_TENDERS = 100
FIELDS = ("code", "name", "active", "tender_group", "requires_reference", "max_amount")


class TenderPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["zetsa.tender-types"]
    version: Literal[1]
    tenders: list[dict] = Field(max_length=MAX_TENDERS)


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package: TenderPackage


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


def _message(exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        return str(exc.detail)
    if isinstance(exc, ValidationError):
        first = exc.errors()[0]
        place = ".".join(str(part) for part in first["loc"])
        return f"{place}: {first['msg']}" if place else first["msg"]
    return str(exc)


async def _current(db: AsyncSession, *, lock: bool = False) -> dict[str, FoundationReference]:
    query = select(FoundationReference).where(FoundationReference.category == "Tender")
    if lock:
        query = query.with_for_update()
    return {row.code: row for row in (await db.execute(query)).scalars().all()}


def _business(row: FoundationReference) -> dict:
    return {key: value for key, value in audit_state(row).items() if key in FIELDS}


def _packaged(body: TenderBody) -> dict:
    attributes = body.attributes()
    return {
        "code": body.code,
        "name": body.name,
        "active": body.active,
        "tender_group": attributes["tender_group"],
        "requires_reference": attributes["requires_reference"],
        "max_amount": attributes["max_amount"],
    }


def _plan(package: TenderPackage, current: dict[str, FoundationReference]):
    normalized: list[tuple[TenderBody, dict]] = []
    errors: list[dict] = []
    seen: set[str] = set()
    for row_number, raw in enumerate(package.tenders, start=1):
        try:
            unknown = set(raw) - set(FIELDS)
            if unknown:
                raise ValueError(f"Unknown field {sorted(unknown)[0]!r}")
            body = TenderBody(**raw)
            if body.code in seen:
                raise ValueError(f"Duplicate tender code {body.code!r} in package")
            require_known_code(body.code)
            packaged = _packaged(body)
        except (ValueError, ValidationError, HTTPException) as exc:
            errors.append({"row": row_number, "error": _message(exc)})
            continue
        seen.add(body.code)
        normalized.append((body, packaged))
    rows = []
    for _, packaged in normalized:
        existing = current.get(packaged["code"])
        if existing is None:
            action = "Create"
        elif all(_business(existing).get(key) == value for key, value in packaged.items()):
            action = "Unchanged"
        else:
            action = "Update"
        rows.append({"code": packaged["code"], "name": packaged["name"], "action": action})
    canonical = json.dumps(
        {
            "package": [packaged for _, packaged in normalized],
            "current": {code: _business(row) for code, row in sorted(current.items())},
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return normalized, errors, rows, hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@router.get("")
async def export_tender_package(
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("analyst", "finance", "it", "auditor")),
):
    current = await _current(db)
    return {
        "format": "zetsa.tender-types",
        "version": 1,
        "tenders": [
            {key: value for key, value in view(row).items() if key in FIELDS}
            for _, row in sorted(current.items())
        ],
    }


@router.post("/preview")
async def preview_tender_package(
    body: PreviewRequest,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("finance", "it")),
):
    normalized, errors, rows, token = _plan(body.package, await _current(db))
    changes = sum(1 for row in rows if row["action"] != "Unchanged")
    return {
        "valid": not errors and bool(normalized),
        "preview_token": token if not errors and normalized else None,
        "changed": bool(changes) and not errors,
        "errors": errors,
        "rows": rows,
        "counts": {
            "create": sum(1 for row in rows if row["action"] == "Create"),
            "update": sum(1 for row in rows if row["action"] == "Update"),
            "unchanged": sum(1 for row in rows if row["action"] == "Unchanged"),
        },
    }


@router.post("/apply")
async def apply_tender_package(
    body: ApplyRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it")),
):
    current = await _current(db, lock=True)
    normalized, errors, rows, token = _plan(body.package, current)
    if token != body.preview_token:
        raise HTTPException(
            status_code=409,
            detail="Tender types changed after preview. Preview the package again.",
        )
    if errors:
        raise HTTPException(
            status_code=422,
            detail={"message": "Tender package validation failed", "errors": errors},
        )
    actions = {row["code"]: row["action"] for row in rows}
    if not any(action != "Unchanged" for action in actions.values()):
        return {"changed": False}
    actor = user.get("user") or "ontology.user"
    for tender, packaged in normalized:
        action = actions[tender.code]
        if action == "Unchanged":
            continue
        if action == "Create":
            row = FoundationReference(
                id=str(uuid.uuid4()),
                category="Tender",
                code=tender.code,
                name=tender.name,
                active=tender.active,
                attributes=tender.attributes(),
                updated_by=actor,
            )
            db.add(row)
            before = None
        else:
            row = current[tender.code]
            before = audit_state(row)
            row.name, row.active, row.updated_by = tender.name, tender.active, actor
            row.attributes = tender.attributes(
                row.attributes if isinstance(row.attributes, dict) else {}
            )
        audit(
            db,
            "TENDER_TYPE_PACKAGE_APPLIED",
            None,
            "FoundationReference",
            row.id,
            f"Tender type {row.code} {action.lower()}d by package: {body.reason}",
            actor=actor,
            after={"before": before, "after": packaged},
        )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Tender types changed during promotion. Preview the package again.",
        ) from exc
    return {
        "changed": True,
        "created": sum(1 for action in actions.values() if action == "Create"),
        "updated": sum(1 for action in actions.values() if action == "Update"),
    }
