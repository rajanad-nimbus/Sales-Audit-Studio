"""Governance API for retailer-authored transaction attribute rules."""

import hashlib
import json
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from database import get_db
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from integrations.ontology.nimbus import audit
from model_base import utcnow
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from sqlalchemy import and_, exists, func, not_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.controls.rules.transaction_logic import sql_condition
from apps.controls.rules.transaction_models import ConfiguredTransactionAuditRule
from apps.csv_export import csv_row
from apps.database_errors import violates_unique_constraint
from apps.security import scope
from apps.security.authentication import require_role
from apps.store_days.freshness import lock_configuration
from apps.store_days.models import StoreDay
from apps.totals.calculation import store_location_traits
from apps.totals.models import SalesAuditErrorDefinition
from apps.transactions.models import CanonicalTransaction

router = APIRouter(prefix="/api/transaction-audit-rules", tags=["Transaction audit rules"])

FIELD_TYPES = {
    "business_date": "text",
    "transaction_type": "text",
    "transaction_subtype": "text",
    "source_lineage": "text",
    "currency": "text",
    "signed_amount": "number",
    "quantity": "number",
    "register_id": "text",
    "cashier_id": "text",
    "salesperson_id": "text",
    "channel_id": "text",
    "tender_type": "text",
    "detail_status": "text",
    "return_reason": "text",
    "transaction_number": "text",
}
RECORD_TYPES = {"Sale", "Return", "Order", "Refund"}


class ConditionBody(BaseModel):
    field: Literal[
        "business_date",
        "transaction_type",
        "transaction_subtype",
        "source_lineage",
        "currency",
        "signed_amount",
        "quantity",
        "register_id",
        "cashier_id",
        "salesperson_id",
        "channel_id",
        "tender_type",
        "detail_status",
        "return_reason",
        "transaction_number",
    ]
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte", "contains", "is_empty", "not_empty"]
    value: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def validate_operator_and_value(self):
        if self.operator in {"is_empty", "not_empty"}:
            if self.value not in (None, ""):
                raise ValueError("Empty checks do not take a comparison value")
            return self
        if self.value is None or not self.value.strip():
            raise ValueError("This comparison requires a value")
        if self.operator in {"gt", "gte", "lt", "lte"} and FIELD_TYPES[self.field] != "number":
            raise ValueError("Ordered comparisons are supported only for numeric fields")
        if self.field in {"signed_amount", "quantity"}:
            try:
                number = Decimal(self.value)
            except InvalidOperation as exc:
                raise ValueError("Enter a valid numeric comparison value") from exc
            if not number.is_finite():
                raise ValueError("Enter a finite numeric comparison value")
        return self


class TransactionRuleBody(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    error_code: str = Field(min_length=1, max_length=64)
    record_type: str | None = Field(default=None, max_length=50)
    conditions: list[ConditionBody] = Field(min_length=1, max_length=20)
    match_mode: Literal["Match", "No Match"] = "Match"
    evaluation_scope: Literal["Transaction", "StoreDayAbsence"] = "Transaction"
    start_date: str | None = Field(default=None, min_length=10, max_length=10)
    end_date: str | None = Field(default=None, min_length=10, max_length=10)
    location_traits: list[str] = Field(default_factory=list, max_length=100)
    execution_order: int = Field(default=100, ge=0, le=100_000)
    stop_processing: bool = False
    owner: str | None = Field(default=None, max_length=255)

    @field_validator("name", "error_code")
    @classmethod
    def normalize_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Value cannot be blank")
        return value

    @field_validator("record_type")
    @classmethod
    def validate_record_type(cls, value: str | None) -> str | None:
        if value is not None and value not in RECORD_TYPES:
            raise ValueError("Choose Sale, Return, Order, or Refund")
        return value

    @field_validator("start_date", "end_date")
    @classmethod
    def validate_date(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("Dates must use YYYY-MM-DD") from exc
        if parsed.isoformat() != value:
            raise ValueError("Dates must use YYYY-MM-DD")
        return value

    @field_validator("location_traits")
    @classmethod
    def normalize_traits(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value or len(value) > 100 for value in normalized):
            raise ValueError("Location traits must be nonblank codes of at most 100 characters")
        if len(set(normalized)) != len(normalized):
            raise ValueError("Location traits must be unique")
        return sorted(normalized)

    @field_validator("owner")
    @classmethod
    def normalize_owner(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @model_validator(mode="after")
    def validate_date_range(self):
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("End date must be on or after start date")
        if self.evaluation_scope == "StoreDayAbsence" and self.match_mode != "Match":
            raise ValueError("Store Day absence rules evaluate the matching conditions")
        return self


class TransactionRulePackageBody(BaseModel):
    format: Literal["zetsa.transaction-audit-rules"]
    version: Literal[1]
    rules: list[dict] = Field(min_length=1, max_length=100)


class TransactionRulePackageApplyBody(TransactionRulePackageBody):
    preview_token: str = Field(pattern=r"^[a-f0-9]{64}$")


class RejectBody(BaseModel):
    rationale: str = Field(min_length=1, max_length=2000)


def view(row: ConfiguredTransactionAuditRule) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "error_code": row.error_code,
        "record_type": row.record_type,
        "conditions": row.conditions or [],
        "match_mode": row.match_mode,
        "evaluation_scope": row.evaluation_scope or "Transaction",
        "start_date": row.start_date,
        "end_date": row.end_date,
        "location_traits": row.location_traits or [],
        "execution_order": row.execution_order,
        "stop_processing": row.stop_processing,
        "status": row.status,
        "revision": row.revision,
        "owner": row.owner,
        "enabled": row.enabled,
        "submitted_by": row.submitted_by,
        "submitted_at": row.submitted_at,
        "approved_by": row.approved_by,
        "approved_at": row.approved_at,
        "updated_by": row.updated_by,
        "updated_at": row.updated_at,
    }


def duplicate_rule_name(error: IntegrityError) -> bool:
    return violates_unique_constraint(
        error,
        constraint_names={"uq_configured_transaction_audit_rule_name"},
        sqlite_messages={"UNIQUE constraint failed: configured_transaction_audit_rules.name"},
    )


@router.get("/{rule_id}/preview")
async def preview_transaction_rule(
    rule_id: str,
    date_from: str = Query(min_length=10, max_length=10),
    date_to: str = Query(min_length=10, max_length=10),
    store_id: str | None = Query(default=None, min_length=1, max_length=50),
    format: Literal["json", "csv"] = Query(default="json"),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it", "admin")),
):
    """Show matching transaction records without creating cases or changing status."""
    rule = await db.get(ConfiguredTransactionAuditRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Transaction audit rule not found")
    if rule.status != "Worksheet":
        raise HTTPException(status_code=409, detail="Only worksheet rules can be previewed")
    try:
        start, end = date.fromisoformat(date_from), date.fromisoformat(date_to)
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
            raise HTTPException(status_code=422, detail="Choose a store for a location-scoped rule")
        try:
            traits = await store_location_traits(db, store_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not set(rule.location_traits).intersection(traits):
            raise HTTPException(status_code=422, detail="The selected store is outside this rule")

    if rule.evaluation_scope == "StoreDayAbsence":
        candidate_days = select(
            StoreDay.id.label("id"),
            StoreDay.store_id.label("store_id"),
            StoreDay.business_date.label("business_date"),
        ).where(
            StoreDay.status == "Open",
            StoreDay.business_date >= date_from,
            StoreDay.business_date <= date_to,
        )
        if store_id:
            candidate_days = candidate_days.where(StoreDay.store_id == store_id)
        if rule.start_date:
            candidate_days = candidate_days.where(StoreDay.business_date >= rule.start_date)
        if rule.end_date:
            candidate_days = candidate_days.where(StoreDay.business_date <= rule.end_date)
        candidate_days = scope.restrict(candidate_days, user, StoreDay.store_id).subquery()
        match_filters = [
            CanonicalTransaction.store_id == candidate_days.c.store_id,
            CanonicalTransaction.business_date == candidate_days.c.business_date,
            CanonicalTransaction.source_lineage.in_(("pos-map-v1", "shopify-map-v1")),
        ]
        if rule.record_type:
            match_filters.append(CanonicalTransaction.transaction_type == rule.record_type)
        match_filters.extend(sql_condition(condition) for condition in rule.conditions or [])
        matches = select(CanonicalTransaction.id).where(*match_filters).correlate(candidate_days)
        absence_query = select(
            candidate_days.c.store_id,
            candidate_days.c.business_date,
        ).where(not_(exists(matches)))
        export_query = absence_query.order_by(
            candidate_days.c.business_date.desc(), candidate_days.c.store_id
        )
        evaluated = (
            await db.execute(select(func.count()).select_from(candidate_days))
        ).scalar_one()
        total = (
            await db.execute(select(func.count()).select_from(absence_query.subquery()))
        ).scalar_one()
        sample_rows = (
            await db.execute(
                absence_query.order_by(
                    candidate_days.c.business_date.desc(), candidate_days.c.store_id
                ).limit(50)
            )
        ).all()
        samples = [
            {
                "transaction_id": None,
                "store_id": store,
                "business_date": business_date,
                "record_type": rule.record_type or "Store Day",
                "amount": "0",
                "currency": None,
                "register_id": None,
                "cashier_id": None,
            }
            for store, business_date in sample_rows
        ]
    else:
        match = and_(*(sql_condition(condition) for condition in rule.conditions or []))
        predicate = match if rule.match_mode == "Match" else not_(match)
        candidate_query = select(CanonicalTransaction).where(
            CanonicalTransaction.business_date >= date_from,
            CanonicalTransaction.business_date <= date_to,
            CanonicalTransaction.source_lineage.in_(("pos-map-v1", "shopify-map-v1")),
        )
        if rule.record_type:
            candidate_query = candidate_query.where(
                CanonicalTransaction.transaction_type == rule.record_type
            )
        if store_id:
            candidate_query = candidate_query.where(CanonicalTransaction.store_id == store_id)
        if rule.start_date:
            candidate_query = candidate_query.where(
                CanonicalTransaction.business_date >= rule.start_date
            )
        if rule.end_date:
            candidate_query = candidate_query.where(
                CanonicalTransaction.business_date <= rule.end_date
            )
        candidate_query = scope.restrict(candidate_query, user, CanonicalTransaction.store_id)
        query = candidate_query.where(predicate)
        export_query = query.order_by(
            CanonicalTransaction.business_date.desc(),
            CanonicalTransaction.store_id,
            CanonicalTransaction.id,
        )
        evaluated = (
            await db.execute(select(func.count()).select_from(candidate_query.subquery()))
        ).scalar_one()
        total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
        sample_rows = (
            await db.execute(
                query.order_by(
                    CanonicalTransaction.business_date.desc(),
                    CanonicalTransaction.store_id,
                    CanonicalTransaction.id,
                ).limit(50)
            )
        ).scalars()
        samples = [
            {
                "transaction_id": row.id,
                "store_id": row.store_id,
                "business_date": row.business_date,
                "record_type": row.transaction_type,
                "amount": str(row.signed_amount),
                "currency": row.currency,
                "register_id": row.register_id,
                "cashier_id": row.cashier_id,
            }
            for row in sample_rows
        ]
    if format == "csv":
        row_limit = 50_000
        columns = (
            "transaction_id",
            "store_id",
            "business_date",
            "record_type",
            "signed_amount",
            "currency",
            "register_id",
            "cashier_id",
        )

        async def generate():
            yield csv_row(columns)
            result = await db.stream(
                export_query.limit(row_limit).execution_options(yield_per=1000)
            )
            try:
                if rule.evaluation_scope == "StoreDayAbsence":
                    async for store, business_date in result:
                        yield csv_row(
                            (
                                "",
                                store,
                                business_date,
                                rule.record_type or "Store Day",
                                "0",
                                "",
                                "",
                                "",
                            )
                        )
                else:
                    async for row in result.scalars():
                        yield csv_row(
                            (
                                row.id,
                                row.store_id,
                                row.business_date,
                                row.transaction_type,
                                row.signed_amount,
                                row.currency,
                                row.register_id,
                                row.cashier_id,
                            )
                        )
            finally:
                await result.close()

        return StreamingResponse(
            generate(),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="audit-rule-preview-{rule.id}.csv"',
                "X-Preview-Truncated": "true" if total > row_limit else "false",
            },
        )

    return {
        "rule_id": rule.id,
        "rule_name": rule.name,
        "error_code": rule.error_code,
        "match_mode": rule.match_mode,
        "evaluation_scope": rule.evaluation_scope or "Transaction",
        "date_from": date_from,
        "date_to": date_to,
        "evaluated": evaluated,
        "would_trigger": total,
        "sample_limit": 50,
        "samples": samples,
    }


async def validate_body(db: AsyncSession, body: TransactionRuleBody) -> None:
    error = await db.get(SalesAuditErrorDefinition, body.error_code)
    if error is None or not error.active:
        raise HTTPException(status_code=422, detail="Choose an active Sales Audit error code")
    if body.location_traits:
        from apps.controls.foundation.models import FoundationReference

        stores = (
            await db.execute(
                select(FoundationReference.attributes).where(
                    FoundationReference.category == "Store",
                    FoundationReference.active.is_(True),
                )
            )
        ).scalars()
        active_traits = {
            trait
            for attributes in stores
            if isinstance(attributes, dict)
            for trait in attributes.get("location_traits", [])
            if isinstance(trait, str)
        }
        if active_traits != set(body.location_traits):
            raise HTTPException(status_code=422, detail="Choose active store location traits")


async def validate_rule_package(db: AsyncSession, package: TransactionRulePackageBody):
    """Validate every imported definition and fingerprint the set of existing rule names."""
    errors = []
    normalized: list[tuple[int, TransactionRuleBody]] = []
    seen: set[str] = set()
    for row_number, raw_rule in enumerate(package.rules, start=1):
        try:
            rule = TransactionRuleBody.model_validate(raw_rule)
            key = rule.name.casefold()
            if key in seen:
                raise ValueError(f"Duplicate rule name {rule.name!r} in package")
            seen.add(key)
            await validate_body(db, rule)
            normalized.append((row_number, rule))
        except ValidationError as exc:
            message = "; ".join(
                f"{'.'.join(str(part) for part in issue['loc'])}: {issue['msg']}"
                for issue in exc.errors()
            )
            errors.append({"row": row_number, "message": message})
        except (ValueError, HTTPException) as exc:
            message = exc.detail if isinstance(exc, HTTPException) else str(exc)
            errors.append({"row": row_number, "message": str(message)})

    existing_names = sorted(
        (await db.execute(select(ConfiguredTransactionAuditRule.name))).scalars().all()
    )
    existing_keys = {name.casefold() for name in existing_names}
    for row_number, rule in normalized:
        if rule.name.casefold() in existing_keys:
            errors.append({"row": row_number, "message": f"Rule name {rule.name!r} already exists"})
    token = hashlib.sha256(
        json.dumps(
            {
                "rules": [rule.model_dump() for _, rule in normalized],
                "existing_names": existing_names,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return normalized, errors, token


@router.get("")
async def list_transaction_rules(
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("finance", "it", "analyst", "auditor", "admin")),
):
    rows = (
        await db.execute(
            select(ConfiguredTransactionAuditRule).order_by(
                ConfiguredTransactionAuditRule.execution_order,
                ConfiguredTransactionAuditRule.name,
            )
        )
    ).scalars()
    return [view(row) for row in rows]


@router.post("")
async def create_transaction_rule(
    body: TransactionRuleBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "admin")),
):
    await validate_body(db, body)
    actor = user.get("user") or "ontology.user"
    row = ConfiguredTransactionAuditRule(
        id=str(uuid.uuid4()),
        **body.model_dump(exclude={"conditions"}),
        conditions=[condition.model_dump() for condition in body.conditions],
        status="Worksheet",
        revision=1,
        enabled=False,
        updated_by=actor,
    )
    db.add(row)
    audit(
        db,
        "TRANSACTION_AUDIT_RULE_CREATED",
        None,
        "ConfiguredTransactionAuditRule",
        row.id,
        f"Created transaction audit rule worksheet {row.name}",
        actor=actor,
        after={
            "status": row.status,
            "revision": row.revision,
            "error_code": row.error_code,
            "record_type": row.record_type,
            "conditions": row.conditions,
            "match_mode": row.match_mode,
            "evaluation_scope": row.evaluation_scope,
            "start_date": row.start_date,
            "end_date": row.end_date,
            "location_traits": row.location_traits or [],
            "execution_order": row.execution_order,
            "stop_processing": row.stop_processing,
        },
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if duplicate_rule_name(exc):
            raise HTTPException(status_code=409, detail="Rule name already exists") from exc
        raise
    return view(row)


@router.post("/import/preview")
async def preview_transaction_rule_package(
    body: TransactionRulePackageBody,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("finance", "admin")),
):
    """Validate an exported package without creating or activating any rules."""
    normalized, errors, token = await validate_rule_package(db, body)
    return {
        "valid": not errors,
        "preview_token": token if not errors else None,
        "errors": errors,
        "rows": [
            {
                "row": row_number,
                "name": rule.name,
                "error_code": rule.error_code,
                "condition_count": len(rule.conditions),
                "action": "Create worksheet",
            }
            for row_number, rule in normalized
        ],
        "counts": {"worksheets": len(normalized) if not errors else 0},
    }


@router.post("/import/apply")
async def import_transaction_rule_package(
    body: TransactionRulePackageApplyBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "admin")),
):
    """Create imported definitions as disabled worksheets; imported state never activates rules."""
    normalized, errors, token = await validate_rule_package(db, body)
    if token != body.preview_token:
        raise HTTPException(
            status_code=409,
            detail=(
                "Rule definitions changed after preview. Preview the package again before importing it."
            ),
        )
    if errors:
        raise HTTPException(
            status_code=422,
            detail={"message": "Rule package validation failed", "errors": errors},
        )
    actor = user.get("user") or "ontology.user"
    created = []
    for _, rule in normalized:
        values = rule.model_dump(exclude={"conditions"})
        row = ConfiguredTransactionAuditRule(
            id=str(uuid.uuid4()),
            **values,
            conditions=[condition.model_dump() for condition in rule.conditions],
            status="Worksheet",
            revision=1,
            enabled=False,
            submitted_by=None,
            submitted_at=None,
            approved_by=None,
            approved_at=None,
            updated_by=actor,
        )
        db.add(row)
        audit(
            db,
            "TRANSACTION_AUDIT_RULE_IMPORTED",
            None,
            "ConfiguredTransactionAuditRule",
            row.id,
            f"Imported transaction audit rule worksheet {row.name}",
            actor=actor,
            after={
                "status": row.status,
                "revision": row.revision,
                "error_code": row.error_code,
                "record_type": row.record_type,
                "conditions": row.conditions,
                "match_mode": row.match_mode,
                "evaluation_scope": row.evaluation_scope,
                "start_date": row.start_date,
                "end_date": row.end_date,
                "location_traits": row.location_traits or [],
                "execution_order": row.execution_order,
                "stop_processing": row.stop_processing,
            },
        )
        created.append(row)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if duplicate_rule_name(exc):
            raise HTTPException(
                status_code=409,
                detail="A rule name was created after preview. Preview the package again.",
            ) from exc
        raise
    return {"created": len(created), "rules": [view(row) for row in created]}


@router.put("/{rule_id}")
async def update_transaction_rule(
    rule_id: str,
    body: TransactionRuleBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "admin")),
):
    row = (
        await db.execute(
            select(ConfiguredTransactionAuditRule)
            .where(ConfiguredTransactionAuditRule.id == rule_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Transaction audit rule not found")
    if row.status != "Worksheet":
        raise HTTPException(status_code=409, detail="Only worksheet rules can be edited")
    await validate_body(db, body)
    before = view(row)
    for key, value in body.model_dump(exclude={"conditions"}).items():
        setattr(row, key, value)
    row.conditions = [condition.model_dump() for condition in body.conditions]
    row.revision += 1
    row.enabled = False
    actor = user.get("user") or "ontology.user"
    row.updated_by = actor
    row.updated_at = utcnow()
    audit(
        db,
        "TRANSACTION_AUDIT_RULE_WORKSHEET_UPDATED",
        None,
        "ConfiguredTransactionAuditRule",
        row.id,
        f"Updated transaction audit rule worksheet {row.name} revision {row.revision}",
        actor=actor,
        after={
            "before": before,
            "revision": row.revision,
            "status": row.status,
            "error_code": row.error_code,
            "record_type": row.record_type,
            "conditions": row.conditions,
            "match_mode": row.match_mode,
            "evaluation_scope": row.evaluation_scope,
            "start_date": row.start_date,
            "end_date": row.end_date,
            "location_traits": row.location_traits or [],
            "execution_order": row.execution_order,
            "stop_processing": row.stop_processing,
        },
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if duplicate_rule_name(exc):
            raise HTTPException(status_code=409, detail="Rule name already exists") from exc
        raise
    return view(row)


@router.post("/{rule_id}/submit")
async def submit_transaction_rule(
    rule_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "admin")),
):
    row = await db.get(ConfiguredTransactionAuditRule, rule_id, with_for_update=True)
    if row is None:
        raise HTTPException(status_code=404, detail="Transaction audit rule not found")
    if row.status != "Worksheet":
        raise HTTPException(status_code=409, detail="Only worksheet rules can be submitted")
    actor = user.get("user") or "ontology.user"
    row.status, row.submitted_by, row.submitted_at = "Submitted", actor, utcnow()
    audit(
        db,
        "TRANSACTION_AUDIT_RULE_SUBMITTED",
        None,
        "ConfiguredTransactionAuditRule",
        row.id,
        f"Submitted transaction audit rule {row.name} for approval",
        actor=actor,
        after={"status": row.status, "submitted_by": actor, "revision": row.revision},
    )
    await db.commit()
    return view(row)


@router.post("/{rule_id}/approve")
async def approve_transaction_rule(
    rule_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "admin")),
):
    await lock_configuration(db, exclusive=True)
    row = await db.get(ConfiguredTransactionAuditRule, rule_id, with_for_update=True)
    if row is None:
        raise HTTPException(status_code=404, detail="Transaction audit rule not found")
    if row.status != "Submitted":
        raise HTTPException(status_code=409, detail="Only submitted rules can be approved")
    actor = user.get("user") or "ontology.user"
    if row.submitted_by == actor:
        raise HTTPException(status_code=409, detail="A different approver is required")
    row.status, row.enabled, row.approved_by, row.approved_at = "Active", True, actor, utcnow()
    row.updated_by, row.updated_at = actor, utcnow()
    audit(
        db,
        "TRANSACTION_AUDIT_RULE_APPROVED",
        None,
        "ConfiguredTransactionAuditRule",
        row.id,
        f"Approved transaction audit rule {row.name}",
        actor=actor,
        after={"status": row.status, "approved_by": actor, "revision": row.revision},
    )
    await db.commit()
    return view(row)


@router.post("/{rule_id}/reject")
async def reject_transaction_rule(
    rule_id: str,
    body: RejectBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "admin")),
):
    row = await db.get(ConfiguredTransactionAuditRule, rule_id, with_for_update=True)
    if row is None:
        raise HTTPException(status_code=404, detail="Transaction audit rule not found")
    if row.status != "Submitted":
        raise HTTPException(status_code=409, detail="Only submitted rules can be returned")
    actor = user.get("user") or "ontology.user"
    row.status, row.enabled = "Worksheet", False
    row.submitted_by, row.submitted_at = None, None
    row.updated_by, row.updated_at = actor, utcnow()
    audit(
        db,
        "TRANSACTION_AUDIT_RULE_RETURNED",
        None,
        "ConfiguredTransactionAuditRule",
        row.id,
        f"Returned transaction audit rule {row.name}: {body.rationale.strip()}",
        actor=actor,
        after={"status": row.status, "rationale": body.rationale.strip()},
    )
    await db.commit()
    return view(row)


@router.post("/{rule_id}/disable")
async def disable_transaction_rule(
    rule_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "admin")),
):
    await lock_configuration(db, exclusive=True)
    row = await db.get(ConfiguredTransactionAuditRule, rule_id, with_for_update=True)
    if row is None:
        raise HTTPException(status_code=404, detail="Transaction audit rule not found")
    if row.status != "Active":
        raise HTTPException(status_code=409, detail="Only active rules can be disabled")
    actor = user.get("user") or "ontology.user"
    row.status, row.enabled, row.updated_by, row.updated_at = "Disabled", False, actor, utcnow()
    audit(
        db,
        "TRANSACTION_AUDIT_RULE_DISABLED",
        None,
        "ConfiguredTransactionAuditRule",
        row.id,
        f"Disabled transaction audit rule {row.name}",
        actor=actor,
        after={"status": row.status, "revision": row.revision},
    )
    await db.commit()
    return view(row)
