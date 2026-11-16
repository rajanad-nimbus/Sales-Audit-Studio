"""Versioned, preview-first reconciliation policy promotion."""

import hashlib
import json
import uuid
from decimal import Decimal
from typing import Literal

from database import get_db
from fastapi import APIRouter, Depends, HTTPException
from model_base import utcnow
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.audit.models import AuditEvent
from apps.ingest.models import ReconciliationPolicy
from apps.security.authentication import require_role
from apps.totals.currency import validate_currency

router = APIRouter()


class ReconciliationPolicyConfig(BaseModel):
    amount_tolerance: Decimal = Field(ge=0, le=Decimal("9999999999999.99"), decimal_places=2)
    amount_tolerance_currency: str = Field(min_length=3, max_length=3)
    settlement_day_tolerance: int = Field(ge=0, le=31)
    duplicate_lookback_days: int = Field(ge=0, le=365)
    gap_min_count: int = Field(ge=1)
    gap_share: Decimal = Field(gt=0, le=1, decimal_places=4)

    @field_validator("amount_tolerance_currency")
    @classmethod
    def valid_currency(cls, value: str) -> str:
        normalized = value.strip().upper()
        try:
            validate_currency(normalized)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        return normalized


class ReconciliationPolicyPackage(BaseModel):
    format: Literal["zetsa.reconciliation-policy"]
    version: Literal[1]
    policy: ReconciliationPolicyConfig


class ReconciliationPolicyPackagePreviewRequest(BaseModel):
    package: ReconciliationPolicyPackage


class ReconciliationPolicyPackageApplyRequest(ReconciliationPolicyPackagePreviewRequest):
    preview_token: str = Field(pattern=r"^[a-f0-9]{64}$")
    reason: str = Field(min_length=10, max_length=2000)

    @field_validator("reason")
    @classmethod
    def valid_reason(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 10:
            raise ValueError("Reason must contain at least 10 non-whitespace characters")
        return normalized


def policy_state(policy: ReconciliationPolicy | None) -> dict:
    if policy is None:
        return {
            "amount_tolerance": "0.00",
            "amount_tolerance_currency": "USD",
            "settlement_day_tolerance": 0,
            "duplicate_lookback_days": 0,
            "gap_min_count": 10,
            "gap_share": "0.1000",
        }
    return {
        "amount_tolerance": str(policy.amount_tolerance),
        "amount_tolerance_currency": policy.amount_tolerance_currency,
        "settlement_day_tolerance": policy.settlement_day_tolerance,
        "duplicate_lookback_days": policy.duplicate_lookback_days,
        "gap_min_count": policy.gap_min_count,
        "gap_share": str(policy.gap_share),
    }


def _preview_token(package: ReconciliationPolicyPackage, current: dict) -> str:
    payload = {
        "package": package.policy.model_dump(mode="json"),
        "current": current,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _active_policy(db: AsyncSession, *, lock: bool = False):
    query = (
        select(ReconciliationPolicy)
        .where(ReconciliationPolicy.active.is_(True))
        .order_by(ReconciliationPolicy.updated_at.desc(), ReconciliationPolicy.id.desc())
        .limit(1)
    )
    if lock:
        query = query.with_for_update()
    return (await db.execute(query)).scalar_one_or_none()


def _build_preview(package: ReconciliationPolicyPackage, policy: ReconciliationPolicy | None):
    current = policy_state(policy)
    proposed = package.policy.model_dump(mode="json")
    return {
        "valid": True,
        "preview_token": _preview_token(package, current),
        "changed": proposed != current,
        "current": current,
        "proposed": proposed,
    }


@router.get("/api/reconciliation/policy/package")
async def export_reconciliation_policy_package(
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("finance", "it")),
):
    return {
        "format": "zetsa.reconciliation-policy",
        "version": 1,
        "policy": policy_state(await _active_policy(db)),
    }


@router.post("/api/reconciliation/policy/package/preview")
async def preview_reconciliation_policy_package(
    body: ReconciliationPolicyPackagePreviewRequest,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("finance", "it")),
):
    return _build_preview(body.package, await _active_policy(db))


@router.post("/api/reconciliation/policy/package/apply")
async def apply_reconciliation_policy_package(
    body: ReconciliationPolicyPackageApplyRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance")),
):
    policy = await _active_policy(db, lock=True)
    preview = _build_preview(body.package, policy)
    if preview["preview_token"] != body.preview_token:
        raise HTTPException(
            status_code=409,
            detail="Reconciliation policy changed after preview. Preview the package again.",
        )
    if not preview["changed"]:
        return {"changed": False}

    before_state = policy_state(policy)
    values = body.package.policy
    if policy is None:
        policy = ReconciliationPolicy(
            id=str(uuid.uuid4()), name="Nimbus fallback reconciliation policy", active=True
        )
        db.add(policy)
    policy.amount_tolerance = values.amount_tolerance
    policy.amount_tolerance_currency = values.amount_tolerance_currency
    policy.settlement_day_tolerance = values.settlement_day_tolerance
    policy.duplicate_lookback_days = values.duplicate_lookback_days
    policy.gap_min_count = values.gap_min_count
    policy.gap_share = values.gap_share
    policy.updated_by = user.get("user") or "ontology.user"
    policy.updated_at = utcnow()
    after_state = policy_state(policy)
    db.add(
        AuditEvent(
            id=str(uuid.uuid4()),
            event_type="RECONCILIATION_POLICY_PACKAGE_APPLIED",
            actor=policy.updated_by,
            object_type="ReconciliationPolicy",
            object_id=policy.id,
            action_description=f"Reconciliation policy package applied: {body.reason}",
            before_state=before_state,
            after_state=after_state,
        )
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Reconciliation policy changed during promotion. Preview the package again.",
        ) from exc
    return {"changed": True, "policy": after_state}
