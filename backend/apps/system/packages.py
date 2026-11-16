"""Versioned, preview-first promotion of operational system options."""

import hashlib
import json
import uuid
from typing import Literal

from database import get_db
from fastapi import APIRouter, Depends, HTTPException
from model_base import utcnow
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.audit.models import AuditEvent
from apps.security.authentication import require_role
from apps.store_days.freshness import lock_configuration
from apps.system.models import SystemOption
from apps.system.options import (
    AUTO_AUDIT_AFTER_IMPORT_KEY,
    BACKDATED_TRANSACTION_DAYS_KEY,
    CARD_NUMBER_MASK_KEY,
    CARD_SECURITY_LEVEL_KEY,
    DEFAULT_CARD_NUMBER_MASK,
    DUPLICATE_MISSING_CHECKS_KEY,
    EXPORT_UNIT_KEY,
    STORE_DAY_ORDER_KEY,
    automatic_audit_after_import_enabled,
    credit_card_number_mask,
    credit_card_security_level,
    duplicate_missing_transaction_checks_enabled,
    export_unit_of_work,
    max_backdated_transaction_days,
    store_days_worked_in_order,
    valid_card_number_mask,
)

router = APIRouter(prefix="/api/system-options/package", tags=["System options"])


class SystemOptionsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    export_unit_of_work: Literal["Transaction", "Store Day"] = "Store Day"
    store_days_worked_in_order: bool = False
    max_backdated_transaction_days: int | None = Field(default=None, ge=0, le=3650)
    duplicate_missing_transaction_checks: bool = True
    automatic_audit_after_import: bool = False
    card_security_level: Literal["Restricted Access", "Block"] = "Restricted Access"
    card_number_mask: str = Field(default=DEFAULT_CARD_NUMBER_MASK, min_length=1, max_length=1)

    @field_validator("card_number_mask")
    @classmethod
    def validate_mask(cls, value: str) -> str:
        if not valid_card_number_mask(value):
            raise ValueError("Card number mask must be one printable, non-digit character")
        return value


class SystemOptionsPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["zetsa.system-options"]
    version: Literal[1]
    options: SystemOptionsConfig


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package: SystemOptionsPackage


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


def _config_values(config: SystemOptionsConfig) -> dict[str, str]:
    return {
        EXPORT_UNIT_KEY: config.export_unit_of_work,
        STORE_DAY_ORDER_KEY: str(config.store_days_worked_in_order).lower(),
        BACKDATED_TRANSACTION_DAYS_KEY: (
            str(config.max_backdated_transaction_days)
            if config.max_backdated_transaction_days is not None
            else "unlimited"
        ),
        DUPLICATE_MISSING_CHECKS_KEY: str(config.duplicate_missing_transaction_checks).lower(),
        AUTO_AUDIT_AFTER_IMPORT_KEY: str(config.automatic_audit_after_import).lower(),
        CARD_SECURITY_LEVEL_KEY: config.card_security_level,
        CARD_NUMBER_MASK_KEY: config.card_number_mask,
    }


async def _current_config(db: AsyncSession, *, lock: bool = False) -> dict:
    if lock:
        await db.execute(select(SystemOption).with_for_update())
    return SystemOptionsConfig(
        export_unit_of_work=await export_unit_of_work(db),
        store_days_worked_in_order=await store_days_worked_in_order(db),
        max_backdated_transaction_days=await max_backdated_transaction_days(db),
        duplicate_missing_transaction_checks=await duplicate_missing_transaction_checks_enabled(db),
        automatic_audit_after_import=await automatic_audit_after_import_enabled(db),
        card_security_level=await credit_card_security_level(db),
        card_number_mask=await credit_card_number_mask(db),
    ).model_dump(mode="json")


def _preview(package: SystemOptionsPackage, current: dict) -> dict:
    proposed = package.options.model_dump(mode="json")
    fields = (
        ("export_unit_of_work", "Export unit of work"),
        ("store_days_worked_in_order", "Store days worked in order"),
        ("max_backdated_transaction_days", "Maximum backdated age in days"),
        ("duplicate_missing_transaction_checks", "Duplicate and missing checks"),
        ("automatic_audit_after_import", "Audit after standalone import"),
        ("card_security_level", "Payment card ingestion policy"),
        ("card_number_mask", "Card number mask character"),
    )
    changes = [
        {"key": key, "label": label, "current": current[key], "proposed": proposed[key]}
        for key, label in fields
        if current[key] != proposed[key]
    ]
    payload = {
        "package": package.model_dump(mode="json"),
        "current": current,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return {
        "valid": True,
        "preview_token": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "changed": bool(changes),
        "current": current,
        "proposed": proposed,
        "changes": changes,
    }


@router.get("")
async def export_system_options_package(
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("it", "admin")),
):
    await lock_configuration(db)
    return {
        "format": "zetsa.system-options",
        "version": 1,
        "options": await _current_config(db),
    }


@router.post("/preview")
async def preview_system_options_package(
    body: PreviewRequest,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("it", "admin")),
):
    await lock_configuration(db)
    return _preview(body.package, await _current_config(db))


@router.post("/apply")
async def apply_system_options_package(
    body: ApplyRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("it", "admin")),
):
    await lock_configuration(db, exclusive=True)
    current = await _current_config(db, lock=True)
    preview = _preview(body.package, current)
    if preview["preview_token"] != body.preview_token:
        raise HTTPException(
            status_code=409,
            detail="System options changed after preview. Preview the package again.",
        )
    if not preview["changed"]:
        return {"changed": False}

    actor = user.get("user") or "ontology.user"
    now = utcnow()
    before_values = _config_values(SystemOptionsConfig(**current))
    for key, value in _config_values(body.package.options).items():
        if before_values[key] == value:
            continue
        option = await db.get(SystemOption, key)
        if option is None:
            option = SystemOption(key=key, value=value)
            db.add(option)
        option.value = value
        option.updated_by = actor
        option.updated_at = now

    after = body.package.options.model_dump(mode="json")
    db.add(
        AuditEvent(
            id=str(uuid.uuid4()),
            event_type="SYSTEM_OPTIONS_PACKAGE_APPLIED",
            actor=actor,
            object_type="SystemOptionsPackage",
            object_id="system-options",
            action_description=f"System options package applied: {body.reason}",
            before_state=current,
            after_state=after,
        )
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="System options changed during promotion. Preview the package again.",
        ) from exc
    return {"changed": True, "options": after}
