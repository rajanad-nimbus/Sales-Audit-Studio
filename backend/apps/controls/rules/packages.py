"""Versioned exchange of store-day audit rules between environments.

Exports carry rule definitions only. Importing validates every rule exactly as the rule form does and
creates disabled worksheets, so an imported rule still goes through submit and a second-person approval.
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

from apps.controls.rules.models import ConfiguredAuditRule
from apps.controls.rules.routes import RuleBody, validate_rule_body, view
from apps.security.authentication import require_role

router = APIRouter(prefix="/api/audit-rules/package", tags=["Audit rules"])
MAX_RULES = 500
PACKAGED_FIELDS = (
    "name",
    "total_name",
    "start_date",
    "end_date",
    "location_traits",
    "execution_order",
    "stop_processing",
    "balance_level",
    "comparison",
    "threshold",
    "threshold_currency",
    "severity",
    "owner",
)


class RulePackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["zetsa.audit-rules"]
    version: Literal[1]
    rules: list[dict] = Field(max_length=MAX_RULES)


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package: RulePackage


class ApplyRequest(PreviewRequest):
    preview_token: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("package")
    @classmethod
    def not_empty(cls, value: RulePackage) -> RulePackage:
        if not value.rules:
            raise ValueError("The package contains no rules")
        return value


def _message(exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        return str(exc.detail)
    if isinstance(exc, ValidationError):
        first = exc.errors()[0]
        place = ".".join(str(part) for part in first["loc"])
        return f"{place}: {first['msg']}" if place else first["msg"]
    return str(exc)


async def _validate(db: AsyncSession, package: RulePackage):
    """Return (normalized rules, per-row errors, token) for a package."""
    existing = set((await db.execute(select(ConfiguredAuditRule.name))).scalars().all())
    normalized: list[RuleBody] = []
    errors: list[dict] = []
    seen: set[str] = set()
    for row_number, raw in enumerate(package.rules, start=1):
        try:
            unknown = set(raw) - set(PACKAGED_FIELDS)
            if unknown:
                raise ValueError(f"Unknown field {sorted(unknown)[0]!r}")
            body = RuleBody(**{**raw, "enabled": False})
            if body.name in seen:
                raise ValueError(f"Duplicate rule name {body.name!r} in package")
            if body.name in existing:
                raise ValueError(f"A rule named {body.name!r} already exists")
            await validate_rule_body(body, db)
        except (ValueError, ValidationError, HTTPException) as exc:
            errors.append({"row": row_number, "error": _message(exc)})
            continue
        seen.add(body.name)
        normalized.append(body)
    canonical = json.dumps(
        {
            "rules": [body.model_dump(mode="json") for body in normalized],
            "existing": sorted(existing),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return normalized, errors, hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@router.get("")
async def export_rule_package(
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("finance", "it", "analyst", "auditor", "admin")),
):
    rows = (
        (await db.execute(select(ConfiguredAuditRule).order_by(ConfiguredAuditRule.name)))
        .scalars()
        .all()
    )
    return {
        "format": "zetsa.audit-rules",
        "version": 1,
        "rules": [
            {
                key: (str(value) if key == "threshold" else value)
                for key, value in view(row).items()
                if key in PACKAGED_FIELDS
            }
            for row in rows
        ],
    }


@router.post("/preview")
async def preview_rule_package(
    body: PreviewRequest,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("finance", "admin")),
):
    normalized, errors, token = await _validate(db, body.package)
    return {
        "valid": not errors and bool(normalized),
        "preview_token": token if not errors and normalized else None,
        "errors": errors,
        "rows": [
            {
                "row": index,
                "name": rule.name,
                "total_name": rule.total_name,
                "action": "Create worksheet",
            }
            for index, rule in enumerate(normalized, start=1)
        ],
        "counts": {"worksheets": len(normalized) if not errors else 0},
    }


@router.post("/apply")
async def apply_rule_package(
    body: ApplyRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "admin")),
):
    normalized, errors, token = await _validate(db, body.package)
    if token != body.preview_token:
        raise HTTPException(
            status_code=409,
            detail="Audit rules changed after preview. Preview the package again before importing it.",
        )
    if errors:
        raise HTTPException(
            status_code=422,
            detail={"message": "Rule package validation failed", "errors": errors},
        )
    actor = user.get("user") or "ontology.user"
    created = []
    for rule in normalized:
        row = ConfiguredAuditRule(
            id=str(uuid.uuid4()),
            **rule.model_dump(exclude={"enabled"}),
            enabled=False,
            status="Worksheet",
            revision=1,
            updated_by=actor,
        )
        db.add(row)
        audit(
            db,
            "AUDIT_RULE_IMPORTED",
            None,
            "ConfiguredAuditRule",
            row.id,
            f"Imported audit rule worksheet {row.name}",
            actor=actor,
            after={"status": row.status, "revision": row.revision, "total_name": row.total_name},
        )
        created.append(row)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="A rule name was created after preview. Preview the package again.",
        ) from exc
    return {"created": len(created), "rules": [view(row) for row in created]}
