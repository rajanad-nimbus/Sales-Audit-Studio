"""Versioned, preview-first promotion for voucher aging and jurisdiction policy."""

import hashlib
import json
import uuid
from typing import Literal

from database import get_db
from fastapi import APIRouter, Depends, HTTPException
from model_base import utcnow
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.audit.models import AuditEvent
from apps.security.authentication import require_role
from apps.store_days.freshness import lock_configuration
from apps.vouchers.jurisdictions import (
    Jurisdiction,
    JurisdictionRule,
    RoutingPolicyInput,
    policy_view,
)
from apps.vouchers.models import VoucherOption, VoucherRoutingPolicy

router = APIRouter(prefix="/policy-package", tags=["Vouchers"])


class VoucherOptionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tender_type: str = Field(min_length=1, max_length=100)
    escheat_days: int = Field(ge=0, le=36500)
    purge_days: int | None = Field(default=None, ge=0, le=36500)

    @field_validator("tender_type")
    @classmethod
    def valid_tender_type(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("tender_type cannot be blank")
        return normalized


class VoucherRoutingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    hq_location: Jurisdiction | None = None
    income_partner: str | None = Field(default=None, max_length=100)
    rules: list[JurisdictionRule] = Field(default_factory=list, max_length=200)

    @field_validator("income_partner", mode="before")
    @classmethod
    def normalize_partner(cls, value):
        return value.strip() or None if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_policy(self):
        RoutingPolicyInput(**self.model_dump())
        return self


class VoucherPolicyPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["zetsa.voucher-policy"]
    version: Literal[1]
    options: list[VoucherOptionConfig] = Field(max_length=100)
    routing: VoucherRoutingConfig

    @model_validator(mode="after")
    def unique_tenders(self):
        names = [option.tender_type.casefold() for option in self.options]
        if len(names) != len(set(names)):
            raise ValueError("Voucher tender types must be unique, ignoring case")
        return self


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package: VoucherPolicyPackage


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


def _routing_config(row: VoucherRoutingPolicy | None) -> dict:
    view = policy_view(row)
    return VoucherRoutingConfig(
        enabled=view["enabled"],
        hq_location=view["hq_location"],
        income_partner=view["income_partner"],
        rules=view["rules"] or [],
    ).model_dump(mode="json")


async def _current_state(db: AsyncSession, *, lock: bool = False) -> dict:
    options_query = select(VoucherOption)
    routing_query = select(VoucherRoutingPolicy).where(VoucherRoutingPolicy.id == "default")
    if lock:
        options_query = options_query.with_for_update()
        routing_query = routing_query.with_for_update()
    options = (await db.execute(options_query)).scalars().all()
    routing = (await db.execute(routing_query)).scalar_one_or_none()
    return {
        "options": [
            {
                "tender_type": row.tender_type,
                "escheat_days": row.escheat_days,
                "purge_days": row.purge_days,
            }
            for row in sorted(options, key=lambda option: option.tender_type.casefold())
        ],
        "routing": _routing_config(routing),
    }


def _proposed_state(package: VoucherPolicyPackage) -> dict:
    return {
        "options": [
            option.model_dump(mode="json")
            for option in sorted(package.options, key=lambda row: row.tender_type.casefold())
        ],
        "routing": package.routing.model_dump(mode="json"),
    }


def _token(package: VoucherPolicyPackage, current: dict) -> str:
    payload = {
        "package": package.model_dump(mode="json"),
        "current": current,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _preview(package: VoucherPolicyPackage, current: dict) -> dict:
    proposed = _proposed_state(package)
    current_by_name = {row["tender_type"].casefold(): row for row in current["options"]}
    proposed_by_name = {row["tender_type"].casefold(): row for row in proposed["options"]}
    option_rows = []
    counts = {"create": 0, "update": 0, "unchanged": 0, "remove": 0}
    for key in sorted(set(current_by_name) | set(proposed_by_name)):
        before = current_by_name.get(key)
        after = proposed_by_name.get(key)
        action = "create" if before is None else "remove" if after is None else (
            "unchanged" if before == after else "update"
        )
        counts[action] += 1
        option_rows.append(
            {
                "tender_type": (after or before)["tender_type"],
                "action": action,
                "current": before,
                "proposed": after,
            }
        )
    routing_changed = current["routing"] != proposed["routing"]
    return {
        "valid": True,
        "preview_token": _token(package, current),
        "changed": current != proposed,
        "current": current,
        "proposed": proposed,
        "counts": counts,
        "routing_changed": routing_changed,
        "options": option_rows,
    }


@router.get("")
async def export_voucher_policy_package(
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("it", "admin")),
):
    current = await _current_state(db)
    return {
        "format": "zetsa.voucher-policy",
        "version": 1,
        **current,
    }


@router.post("/preview")
async def preview_voucher_policy_package(
    body: PreviewRequest,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("it", "admin")),
):
    return _preview(body.package, await _current_state(db))


@router.post("/apply")
async def apply_voucher_policy_package(
    body: ApplyRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("it", "admin")),
):
    await lock_configuration(db, exclusive=True)
    current = await _current_state(db, lock=True)
    preview = _preview(body.package, current)
    if preview["preview_token"] != body.preview_token:
        raise HTTPException(
            status_code=409,
            detail="Voucher policy changed after preview. Preview the package again.",
        )
    if not preview["changed"]:
        return {"changed": False, "counts": preview["counts"]}

    actor = user.get("user") or "ontology.user"
    existing = (
        await db.execute(select(VoucherOption).with_for_update())
    ).scalars().all()
    existing_by_name = {row.tender_type.casefold(): row for row in existing}
    for option in body.package.options:
        key = option.tender_type.casefold()
        row = existing_by_name.pop(key, None)
        if row is None:
            row = VoucherOption(tender_type=option.tender_type)
            db.add(row)
        row.tender_type = option.tender_type
        row.escheat_days = option.escheat_days
        row.purge_days = option.purge_days
        row.updated_by = actor
        row.updated_at = utcnow()
    for row in existing_by_name.values():
        await db.delete(row)

    if preview["routing_changed"]:
        policy = (
            await db.execute(
                select(VoucherRoutingPolicy)
                .where(VoucherRoutingPolicy.id == "default")
                .with_for_update()
            )
        ).scalar_one_or_none()
        if policy is None:
            policy = VoucherRoutingPolicy(id="default")
            db.add(policy)
        routing = body.package.routing
        policy.enabled = routing.enabled
        policy.hq_location = routing.hq_location.model_dump() if routing.hq_location else None
        policy.income_partner = routing.income_partner
        policy.rules = [rule.model_dump() for rule in routing.rules]
        policy.revision = str(uuid.uuid4())
        policy.updated_by = actor
        policy.updated_at = utcnow()

    after = _proposed_state(body.package)
    db.add(
        AuditEvent(
            id=str(uuid.uuid4()),
            event_type="VOUCHER_POLICY_PACKAGE_APPLIED",
            actor=actor,
            object_type="VoucherPolicy",
            object_id="default",
            action_description=f"Voucher policy package applied: {body.reason}",
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
            detail="Voucher policy changed during promotion. Preview the package again.",
        ) from exc
    return {"changed": True, "counts": preview["counts"], "policy": after}
