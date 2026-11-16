"""Safe retailer-configured Sales Audit rules."""

import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from database import get_db
from fastapi import APIRouter, Depends, HTTPException, Query
from integrations.ontology.nimbus import audit
from model_base import utcnow
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.controls.rules.models import ConfiguredAuditRule
from apps.database_errors import violates_unique_constraint
from apps.security import scope
from apps.security.authentication import require_role
from apps.store_days.freshness import invalidate_configuration
from apps.store_days.models import AuditTotal, StoreDay
from apps.totals.calculation import store_location_traits
from apps.totals.currency import validate_currency
from apps.totals.models import AuditTotalDefinition
from apps.totals.standard import STANDARD_TOTALS, TAX_TOTALS
from apps.transactions.detail import MAX_MONEY, cent_value

router = APIRouter(prefix="/api/audit-rules", tags=["Audit rules"])


class RuleBody(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    total_name: str = Field(min_length=1, max_length=150)
    start_date: str | None = Field(default=None, min_length=10, max_length=10)
    end_date: str | None = Field(default=None, min_length=10, max_length=10)
    location_traits: list[str] = Field(default_factory=list, max_length=100)
    execution_order: int = Field(default=100, ge=0, le=100_000)
    stop_processing: bool = False
    balance_level: Literal["Store", "Register", "Cashier"] = "Store"
    comparison: Literal["ABS_GT", "GT", "GTE", "LT", "LTE", "EQ", "NE"] = "ABS_GT"
    threshold: Decimal = Field(ge=0, max_digits=15, decimal_places=2)
    threshold_currency: str | None = Field(default="USD", max_length=3)
    severity: str = Field(default="Medium", max_length=20)
    owner: str | None = Field(default=None, max_length=255)
    enabled: bool = True

    @field_validator("name", "total_name")
    @classmethod
    def normalize_required_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Value cannot be blank")
        return normalized

    @field_validator("owner")
    @classmethod
    def normalize_owner(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @field_validator("location_traits")
    @classmethod
    def normalize_location_traits(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value or len(value) > 100 for value in normalized):
            raise ValueError("Location traits must be nonblank codes of at most 100 characters")
        if len(set(normalized)) != len(normalized):
            raise ValueError("Location traits must be unique")
        return sorted(normalized)

    @field_validator("start_date", "end_date")
    @classmethod
    def normalize_rule_date(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            normalized = date.fromisoformat(value).isoformat()
        except ValueError as exc:
            raise ValueError("Dates must use YYYY-MM-DD") from exc
        if normalized != value:
            raise ValueError("Dates must use YYYY-MM-DD")
        return normalized

    @model_validator(mode="after")
    def validate_effective_window(self):
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("End date must be on or after start date")
        return self


class RuleRejectBody(BaseModel):
    rationale: str = Field(min_length=1, max_length=2000)


def view(row: ConfiguredAuditRule):
    return {
        "id": row.id,
        "name": row.name,
        "total_name": row.total_name,
        "start_date": row.start_date,
        "end_date": row.end_date,
        "location_traits": row.location_traits or [],
        "execution_order": row.execution_order,
        "stop_processing": row.stop_processing,
        "balance_level": row.balance_level or "Store",
        "comparison": row.comparison,
        "threshold": row.threshold,
        "threshold_currency": row.threshold_currency,
        "severity": row.severity,
        "owner": row.owner,
        "enabled": row.enabled,
        "status": row.status,
        "revision": row.revision,
        "submitted_by": row.submitted_by,
        "submitted_at": row.submitted_at,
        "approved_by": row.approved_by,
        "approved_at": row.approved_at,
        "updated_by": row.updated_by,
        "updated_at": row.updated_at,
    }


def _is_duplicate_rule_name(exc: IntegrityError) -> bool:
    return violates_unique_constraint(
        exc,
        constraint_names={"configured_audit_rules_name_key"},
        sqlite_messages={"UNIQUE constraint failed: configured_audit_rules.name"},
    )


@router.get("")
async def list_rules(
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("finance", "it", "analyst", "auditor", "admin")),
):
    return [
        view(row)
        for row in (
            await db.execute(select(ConfiguredAuditRule).order_by(ConfiguredAuditRule.name))
        )
        .scalars()
        .all()
    ]


@router.get("/{rule_id}/preview")
async def preview_rule(
    rule_id: str,
    date_from: str = Query(min_length=10, max_length=10),
    date_to: str = Query(min_length=10, max_length=10),
    store_id: str | None = Query(default=None, min_length=1, max_length=50),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it")),
):
    """Evaluate a worksheet threshold against current totals at its balancing level."""
    rule = await db.get(ConfiguredAuditRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Audit rule not found")
    if rule.status != "Worksheet":
        raise HTTPException(status_code=409, detail="Only worksheet rules can be previewed")
    try:
        start = date.fromisoformat(date_from)
        end = date.fromisoformat(date_to)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Dates must use YYYY-MM-DD") from exc
    if start.isoformat() != date_from or end.isoformat() != date_to or start > end:
        raise HTTPException(status_code=422, detail="Enter a valid date range")
    if (end - start).days > 365:
        raise HTTPException(status_code=422, detail="Preview range cannot exceed 366 days")
    if store_id is not None:
        scope.require_store(user, store_id)
    if rule.location_traits:
        if store_id is None:
            raise HTTPException(
                status_code=422,
                detail="Choose a store to preview this location-scoped rule",
            )
        try:
            store_traits = await store_location_traits(db, store_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not set(rule.location_traits).intersection(store_traits):
            raise HTTPException(
                status_code=422,
                detail=f"Store {store_id} is outside this rule's location traits",
            )

    latest_query = (
        select(AuditTotal.store_day_id, func.max(AuditTotal.audit_version).label("version"))
        .join(StoreDay, StoreDay.id == AuditTotal.store_day_id)
        .where(
            StoreDay.totals_stale.is_(False),
            StoreDay.business_date >= date_from,
            StoreDay.business_date <= date_to,
        )
        .group_by(AuditTotal.store_day_id)
    )
    if store_id is not None:
        latest_query = latest_query.where(StoreDay.store_id == store_id)
    if rule.start_date is not None:
        latest_query = latest_query.where(StoreDay.business_date >= rule.start_date)
    if rule.end_date is not None:
        latest_query = latest_query.where(StoreDay.business_date <= rule.end_date)
    latest_versions = scope.restrict(latest_query, user, StoreDay.store_id).subquery()
    query = (
        select(
            StoreDay.store_id,
            StoreDay.business_date,
            AuditTotal.currency,
            AuditTotal.dimension_key,
            func.sum(AuditTotal.calculated_amount).label("calculated_amount"),
        )
        .join(AuditTotal, AuditTotal.store_day_id == StoreDay.id)
        .join(
            latest_versions,
            (latest_versions.c.store_day_id == AuditTotal.store_day_id)
            & (latest_versions.c.version == AuditTotal.audit_version),
        )
        .where(
            StoreDay.totals_stale.is_(False),
            StoreDay.business_date >= date_from,
            StoreDay.business_date <= date_to,
            AuditTotal.total_name == rule.total_name,
            AuditTotal.level == (rule.balance_level or "Store"),
        )
    )
    if store_id is not None:
        query = query.where(StoreDay.store_id == store_id)
    if rule.threshold_currency is not None:
        query = query.where(AuditTotal.currency == rule.threshold_currency)
    if rule.start_date is not None:
        query = query.where(StoreDay.business_date >= rule.start_date)
    if rule.end_date is not None:
        query = query.where(StoreDay.business_date <= rule.end_date)
    query = query.group_by(
        StoreDay.store_id,
        StoreDay.business_date,
        AuditTotal.currency,
        AuditTotal.dimension_key,
    )
    query = scope.restrict(query, user, StoreDay.store_id)

    evaluated_rows = query.subquery()
    value = evaluated_rows.c.calculated_amount
    threshold = rule.threshold
    breach = {
        "ABS_GT": func.abs(value) > threshold,
        "GT": value > threshold,
        "GTE": value >= threshold,
        "LT": value < threshold,
        "LTE": value <= threshold,
        "EQ": value == threshold,
        "NE": value != threshold,
    }[rule.comparison]
    aggregate = select(
        func.count().label("evaluated"),
        func.coalesce(func.sum(case((breach, 1), else_=0)), 0).label("would_breach"),
    ).select_from(evaluated_rows)
    counts = (await db.execute(aggregate)).mappings().one()
    sample_query = (
        select(
            evaluated_rows.c.store_id,
            evaluated_rows.c.business_date,
            evaluated_rows.c.currency,
            evaluated_rows.c.dimension_key,
            evaluated_rows.c.calculated_amount,
            breach.label("would_breach"),
        )
        .order_by(
            evaluated_rows.c.business_date.desc(),
            evaluated_rows.c.store_id,
            evaluated_rows.c.currency,
        )
        .limit(50)
    )
    samples = [
        {
            "store_id": store,
            "business_date": business_date,
            "currency": currency,
            "dimension": dimension,
            "observed": str(observed),
            "would_breach": bool(would_breach),
        }
        for store, business_date, currency, dimension, observed, would_breach in (
            await db.execute(sample_query)
        ).all()
    ]
    return {
        "rule_id": rule.id,
        "rule_name": rule.name,
        "total_name": rule.total_name,
        "comparison": rule.comparison,
        "threshold": str(rule.threshold),
        "threshold_currency": rule.threshold_currency,
        "date_from": date_from,
        "date_to": date_to,
        "effective_start_date": rule.start_date,
        "effective_end_date": rule.end_date,
        "location_traits": rule.location_traits or [],
        "execution_order": rule.execution_order,
        "stop_processing": rule.stop_processing,
        "balance_level": rule.balance_level or "Store",
        "evaluated": counts["evaluated"],
        "would_breach": counts["would_breach"],
        "sample_limit": 50,
        "samples": samples,
    }


@router.post("")
async def create_rule(
    body: RuleBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "admin")),
):
    threshold = await validate_rule_body(body, db)
    row = ConfiguredAuditRule(
        id=str(uuid.uuid4()),
        **body.model_dump(exclude={"threshold", "enabled"}),
        threshold=threshold,
        enabled=False,
        status="Worksheet",
        revision=1,
        updated_by=user["user"] or "ontology.user",
    )
    db.add(row)
    audit(
        db,
        "AUDIT_RULE_CREATED",
        None,
        "ConfiguredAuditRule",
        row.id,
        f"Created audit rule worksheet {row.name}",
        actor=user["user"] or "ontology.user",
        after={
            "status": row.status,
            "revision": row.revision,
            "start_date": row.start_date,
            "end_date": row.end_date,
            "location_traits": row.location_traits or [],
            "execution_order": row.execution_order,
            "stop_processing": row.stop_processing,
            "balance_level": row.balance_level,
        },
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if _is_duplicate_rule_name(exc):
            raise HTTPException(status_code=409, detail="Rule name already exists") from exc
        raise
    return view(row)


async def validate_rule_body(body: RuleBody, db: AsyncSession) -> Decimal:
    try:
        threshold = cent_value(body.threshold, "threshold")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if threshold > MAX_MONEY or body.severity not in {"Low", "Medium", "High", "Critical"}:
        raise HTTPException(
            status_code=422,
            detail="Threshold must be a non-negative amount with at most two decimal places and severity valid",
        )
    if body.total_name.startswith("Configured: "):
        configured_name = body.total_name.removeprefix("Configured: ")
        configured = (
            await db.execute(
                select(AuditTotalDefinition.id, AuditTotalDefinition.level).where(
                    AuditTotalDefinition.name == configured_name,
                    AuditTotalDefinition.status == "Active",
                    AuditTotalDefinition.active.is_(True),
                )
            )
        ).first()
        if configured is None:
            raise HTTPException(status_code=422, detail="Choose an active configured total")
        if configured.level != body.balance_level:
            raise HTTPException(
                status_code=422,
                detail=f"Configured total is evaluated at {configured.level} level",
            )
    elif body.total_name not in {total["name"] for total in (*STANDARD_TOTALS, *TAX_TOTALS)}:
        raise HTTPException(status_code=422, detail="Choose a supported Sales Audit total")
    elif body.balance_level != "Store" and not (
        body.total_name == "POS Net" and body.balance_level == "Register"
    ):
        raise HTTPException(
            status_code=422,
            detail=f"{body.total_name} does not provide {body.balance_level}-level totals",
        )
    if body.threshold_currency is not None:
        try:
            validate_currency(body.threshold_currency)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    else:
        name = (
            body.total_name.removeprefix("Configured: ")
            if body.total_name.startswith("Configured: ")
            else None
        )
        definition = (
            (
                await db.execute(
                    select(AuditTotalDefinition)
                    .where(
                        AuditTotalDefinition.name == name,
                        AuditTotalDefinition.status == "Active",
                        AuditTotalDefinition.active.is_(True),
                    )
                    .order_by(AuditTotalDefinition.revision.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if name
            else None
        )
        if not definition or definition.aggregation != "Count":
            raise HTTPException(
                status_code=422,
                detail="A currency-free threshold requires a configured Count total",
            )
    return threshold


@router.put("/{rule_id}")
async def update_rule(
    rule_id: str,
    body: RuleBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "admin")),
):
    row = (
        await db.execute(
            select(ConfiguredAuditRule).where(ConfiguredAuditRule.id == rule_id).with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Audit rule not found")
    if row.status != "Worksheet":
        raise HTTPException(status_code=409, detail="Only worksheet rules can be edited")
    threshold = await validate_rule_body(body, db)
    before = {
        "name": row.name,
        "total_name": row.total_name,
        "start_date": row.start_date,
        "end_date": row.end_date,
        "location_traits": row.location_traits or [],
        "execution_order": row.execution_order,
        "stop_processing": row.stop_processing,
        "balance_level": row.balance_level or "Store",
        "comparison": row.comparison,
        "threshold": str(row.threshold),
    }
    for key, value in body.model_dump(exclude={"enabled"}).items():
        setattr(row, key, value)
    row.threshold = threshold
    row.revision += 1
    row.enabled = False
    row.updated_by = user["user"] or "ontology.user"
    row.updated_at = utcnow()
    audit(
        db,
        "AUDIT_RULE_WORKSHEET_UPDATED",
        None,
        "ConfiguredAuditRule",
        row.id,
        f"Updated audit rule worksheet {row.name} revision {row.revision}",
        actor=row.updated_by,
        after={
            "before": before,
            "revision": row.revision,
            "name": row.name,
            "total_name": row.total_name,
            "start_date": row.start_date,
            "end_date": row.end_date,
            "location_traits": row.location_traits or [],
            "execution_order": row.execution_order,
            "stop_processing": row.stop_processing,
            "balance_level": row.balance_level,
            "comparison": row.comparison,
            "threshold": str(row.threshold),
        },
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if _is_duplicate_rule_name(exc):
            raise HTTPException(status_code=409, detail="Rule name already exists") from exc
        raise
    return view(row)


@router.post("/{rule_id}/submit")
async def submit_rule(
    rule_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "admin")),
):
    row = (
        await db.execute(
            select(ConfiguredAuditRule).where(ConfiguredAuditRule.id == rule_id).with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Audit rule not found")
    if row.status != "Worksheet":
        raise HTTPException(status_code=409, detail="Only worksheet rules can be submitted")
    actor = user["user"] or "ontology.user"
    row.status, row.submitted_by, row.submitted_at = "Submitted", actor, utcnow()
    row.updated_by = actor
    audit(
        db,
        "AUDIT_RULE_SUBMITTED",
        None,
        "ConfiguredAuditRule",
        row.id,
        f"Submitted audit rule {row.name} revision {row.revision} for approval",
        actor=actor,
        after={"status": row.status, "revision": row.revision, "submitted_by": actor},
    )
    await db.commit()
    return view(row)


@router.post("/{rule_id}/approve")
async def approve_rule(
    rule_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "admin")),
):
    row = (
        await db.execute(
            select(ConfiguredAuditRule).where(ConfiguredAuditRule.id == rule_id).with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Audit rule not found")
    if row.status != "Submitted":
        raise HTTPException(status_code=409, detail="Only submitted rules can be approved")
    actor = user["user"] or "ontology.user"
    if row.submitted_by == actor:
        raise HTTPException(status_code=409, detail="A different user must approve this rule")
    row.status, row.enabled = "Active", True
    row.approved_by, row.approved_at = actor, utcnow()
    row.updated_by = actor
    await invalidate_configuration(
        db, actor=actor, reason=f"Audit rule {row.name} revision {row.revision} approved"
    )
    audit(
        db,
        "AUDIT_RULE_APPROVED",
        None,
        "ConfiguredAuditRule",
        row.id,
        f"Approved and activated audit rule {row.name} revision {row.revision}",
        actor=actor,
        after={"status": row.status, "revision": row.revision, "submitted_by": row.submitted_by},
    )
    await db.commit()
    return view(row)


@router.post("/{rule_id}/reject")
async def reject_rule(
    rule_id: str,
    body: RuleRejectBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "admin")),
):
    reason = body.rationale.strip()
    if not reason or len(reason) > 2000:
        raise HTTPException(status_code=422, detail="A rejection rationale is required")
    row = (
        await db.execute(
            select(ConfiguredAuditRule).where(ConfiguredAuditRule.id == rule_id).with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Audit rule not found")
    if row.status != "Submitted":
        raise HTTPException(status_code=409, detail="Only submitted rules can be rejected")
    actor = user["user"] or "ontology.user"
    row.status, row.submitted_by, row.submitted_at = "Worksheet", None, None
    row.updated_by = actor
    audit(
        db,
        "AUDIT_RULE_REJECTED",
        None,
        "ConfiguredAuditRule",
        row.id,
        f"Returned audit rule {row.name} to worksheet",
        actor=actor,
        after={"status": row.status, "revision": row.revision, "rationale": reason},
    )
    await db.commit()
    return view(row)


@router.post("/{rule_id}/disable")
async def disable_rule(
    rule_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "admin")),
):
    row = (
        await db.execute(
            select(ConfiguredAuditRule).where(ConfiguredAuditRule.id == rule_id).with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Audit rule not found")
    if row.status != "Active":
        raise HTTPException(status_code=409, detail="Only active rules can be disabled")
    actor = user["user"] or "ontology.user"
    row.status, row.enabled, row.updated_by = "Disabled", False, actor
    await invalidate_configuration(
        db, actor=actor, reason=f"Audit rule {row.name} revision {row.revision} disabled"
    )
    audit(
        db,
        "AUDIT_RULE_DISABLED",
        None,
        "ConfiguredAuditRule",
        row.id,
        f"Disabled audit rule {row.name} revision {row.revision}",
        actor=actor,
        after={"status": row.status, "revision": row.revision},
    )
    await db.commit()
    return view(row)
