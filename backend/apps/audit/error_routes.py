"""Audit error overview: every Sales Audit error type, how many are open, and how to fix them."""

import hashlib
import json
import re
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal

from database import get_db
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from integrations.ontology import ontology
from integrations.ontology.nimbus import audit
from model_base import utcnow
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.audit.models import AuditEvent
from apps.audit.patterns import error_patterns
from apps.cases.models import Case
from apps.cases.models import Exception as ExceptionRow
from apps.controls.rules.transaction_models import ConfiguredTransactionAuditRule
from apps.csv_export import csv_row
from apps.database_errors import violates_unique_constraint
from apps.exports.models import ExportDestination
from apps.search import contains_literal
from apps.security import scope
from apps.security.authentication import require_role
from apps.store_days.freshness import lock_configuration
from apps.totals.models import SalesAuditErrorDefinition
from apps.transactions.models import CanonicalTransaction

router = APIRouter(prefix="/api/audit-errors", tags=["Audit errors"])

ROLES = ("analyst", "finance", "it", "auditor")
MAX_CASES = 100
MAX_CASE_EXPORT = 50_000
MAX_ERROR_TYPE_EXPORT = 100_000


class ErrorDefinitionBody(BaseModel):
    code: str = Field(min_length=2, max_length=64)
    description: str = Field(min_length=1, max_length=10000)
    short_description: str = Field(min_length=1, max_length=255)
    form: str | None = Field(default=None, max_length=100)
    tab: str | None = Field(default=None, max_length=100)
    recommended_solution: str = Field(min_length=1, max_length=10000)
    active: bool = True
    export_impact_configured: bool = False
    export_block_destinations: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", normalized):
            raise ValueError("Code must use 2–64 uppercase letters, digits, or underscores")
        return normalized

    @field_validator("description", "short_description", "recommended_solution")
    @classmethod
    def require_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Value cannot be blank")
        return normalized

    @field_validator("form", "tab")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @field_validator("export_block_destinations")
    @classmethod
    def normalize_destinations(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError("Export destinations must be distinct, nonblank names")
        return sorted(normalized)

    @model_validator(mode="after")
    def validate_export_impact(self):
        if not self.export_impact_configured and self.export_block_destinations:
            raise ValueError("Configure export impact before selecting blocked destinations")
        return self


class ErrorDefinitionPackageBody(BaseModel):
    format: Literal["zetsa.sales-audit-error-catalog"]
    version: Literal[1]
    definitions: list[dict] = Field(min_length=1, max_length=500)


class ErrorDefinitionPackageApplyBody(ErrorDefinitionPackageBody):
    preview_token: str = Field(pattern=r"^[a-f0-9]{64}$")
    reason: str = Field(min_length=10, max_length=4000)

    @field_validator("reason")
    @classmethod
    def require_import_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A reason is required")
        return value.strip()


def definition_text(definition: SalesAuditErrorDefinition | None) -> dict:
    """The catalog's wording and where the error is fixed, when the catalog knows this code."""
    if definition is None:
        return {
            "description": None,
            "form": None,
            "tab": None,
            "recommended_solution": None,
            "export_impact_configured": False,
            "export_block_destinations": [],
        }
    return {
        "description": definition.description,
        "form": definition.form,
        "tab": definition.tab,
        "recommended_solution": definition.recommended_solution,
        "export_impact_configured": definition.export_impact_configured,
        "export_block_destinations": definition.export_block_destinations or [],
    }


def error_definition_state(definition: SalesAuditErrorDefinition) -> dict:
    return {
        "code": definition.code,
        "description": definition.description,
        "short_description": definition.short_description,
        "form": definition.form,
        "tab": definition.tab,
        "recommended_solution": definition.recommended_solution,
        "active": definition.active,
        "export_impact_configured": definition.export_impact_configured,
        "export_block_destinations": sorted(definition.export_block_destinations or []),
    }


async def prepare_error_definition_package(db: AsyncSession, package: ErrorDefinitionPackageBody):
    errors: list[dict] = []
    existing_rows = (
        (
            await db.execute(
                select(SalesAuditErrorDefinition).order_by(SalesAuditErrorDefinition.code)
            )
        )
        .scalars()
        .all()
    )
    existing_by_code = {row.code: row for row in existing_rows}
    destination_names = set((await db.execute(select(ExportDestination.name))).scalars())
    active_rule_codes = set(
        (
            await db.execute(
                select(ConfiguredTransactionAuditRule.error_code).where(
                    ConfiguredTransactionAuditRule.status == "Active",
                    ConfiguredTransactionAuditRule.enabled.is_(True),
                )
            )
        ).scalars()
    )
    prepared: list[dict] = []
    seen: set[str] = set()
    for row_number, raw in enumerate(package.definitions, start=1):
        try:
            body = ErrorDefinitionBody.model_validate(raw)
        except ValidationError as exc:
            detail = "; ".join(
                f"{'.'.join(str(part) for part in issue['loc'])}: {issue['msg']}"
                for issue in exc.errors()
            )
            errors.append({"row": row_number, "message": detail})
            continue
        if body.code in seen:
            errors.append({"row": row_number, "message": f"Duplicate error code {body.code}"})
            continue
        seen.add(body.code)
        unknown_destinations = sorted(set(body.export_block_destinations) - destination_names)
        if unknown_destinations:
            errors.append(
                {
                    "row": row_number,
                    "message": "Unknown export destination(s): " + ", ".join(unknown_destinations),
                }
            )
            continue
        existing = existing_by_code.get(body.code)
        if body.code in ontology.EXCEPTION_TYPES and not body.active:
            errors.append(
                {
                    "row": row_number,
                    "message": "Built-in Sales Audit error definitions cannot be deactivated; disable their producing control instead",
                }
            )
            continue
        if (
            existing is not None
            and existing.active
            and not body.active
            and body.code in active_rule_codes
        ):
            errors.append(
                {
                    "row": row_number,
                    "message": "This error code is used by an active transaction audit rule and cannot be deactivated",
                }
            )
            continue
        action = "Create"
        if existing is not None:
            action = (
                "Update" if error_definition_state(existing) != body.model_dump() else "Unchanged"
            )
        prepared.append({"row": row_number, "body": body, "existing": existing, "action": action})

    state = {
        "source": [entry["body"].model_dump() for entry in prepared],
        "target": [error_definition_state(row) for row in existing_rows],
        "destinations": sorted(destination_names),
    }
    token = hashlib.sha256(
        json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return prepared, errors, token


@router.get("/definitions")
async def list_error_definitions(
    db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("finance", "it"))
):
    rows = (
        (
            await db.execute(
                select(SalesAuditErrorDefinition).order_by(SalesAuditErrorDefinition.code)
            )
        )
        .scalars()
        .all()
    )
    return [error_definition_state(row) for row in rows]


@router.get("/definitions/package")
async def export_error_definition_package(
    db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("finance", "it"))
):
    rows = (
        (
            await db.execute(
                select(SalesAuditErrorDefinition).order_by(SalesAuditErrorDefinition.code)
            )
        )
        .scalars()
        .all()
    )
    return {
        "format": "zetsa.sales-audit-error-catalog",
        "version": 1,
        "definitions": [error_definition_state(row) for row in rows],
    }


@router.post("/definitions/package/preview")
async def preview_error_definition_package(
    package: ErrorDefinitionPackageBody,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("finance", "it")),
):
    prepared, errors, token = await prepare_error_definition_package(db, package)
    return {
        "valid": not errors,
        "preview_token": token if not errors else None,
        "errors": errors,
        "counts": {
            action.lower(): sum(entry["action"] == action for entry in prepared)
            for action in ("Create", "Update", "Unchanged")
        },
        "definitions": [
            {"row": entry["row"], **entry["body"].model_dump(), "action": entry["action"]}
            for entry in prepared
        ],
    }


@router.post("/definitions/package/apply")
async def apply_error_definition_package(
    request: ErrorDefinitionPackageApplyBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance")),
):
    await lock_configuration(db, exclusive=True)
    package = ErrorDefinitionPackageBody(
        format=request.format, version=request.version, definitions=request.definitions
    )
    prepared, errors, token = await prepare_error_definition_package(db, package)
    if token != request.preview_token:
        raise HTTPException(
            status_code=409,
            detail="Sales Audit error definitions changed after preview. Review the package again.",
        )
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})

    actor = user["user"] or "ontology.user"
    created = updated = unchanged = 0
    for entry in prepared:
        body: ErrorDefinitionBody = entry["body"]
        row: SalesAuditErrorDefinition | None = entry["existing"]
        if row is None:
            row = SalesAuditErrorDefinition(**body.model_dump())
            db.add(row)
            created += 1
            event_type = "SALES_AUDIT_ERROR_DEFINITION_CREATED"
            before = None
        elif entry["action"] == "Update":
            before = error_definition_state(row)
            for field, value in body.model_dump().items():
                setattr(row, field, value)
            updated += 1
            event_type = "SALES_AUDIT_ERROR_DEFINITION_UPDATED"
        else:
            unchanged += 1
            continue
        audit(
            db,
            event_type,
            None,
            "SalesAuditErrorDefinition",
            row.code,
            f"Imported Sales Audit error definition {row.code}: {request.reason}",
            actor=actor,
            after={
                "before": before,
                "after": error_definition_state(row),
                "reason": request.reason,
                "package_format": request.format,
                "package_version": request.version,
            },
        )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if violates_unique_constraint(
            exc,
            constraint_names={"sales_audit_error_definitions_pkey"},
            sqlite_messages={"UNIQUE constraint failed: sales_audit_error_definitions.code"},
        ):
            raise HTTPException(
                status_code=409,
                detail="A Sales Audit error code changed after preview. Review the package again.",
            ) from exc
        raise
    return {"created": created, "updated": updated, "unchanged": unchanged}


def ontology_text(code: str) -> dict:
    """The ontology's label, family, causes and resolution steps for this exception type."""
    meta = ontology.EXCEPTION_TYPES.get(code)
    if meta is None:
        return {
            "label": None,
            "family": None,
            "known_causes": [],
            "resolution": None,
            "steps": None,
        }
    kind, _handling, _exposure, steps = meta["disposition"]
    return {
        "label": meta["label"],
        "family": meta["family"],
        "known_causes": meta["known_causes"],
        "resolution": kind,
        "steps": steps,
    }


OPEN = Case.status != "Closed"
DEFAULT_WINDOW_DAYS = 90


class ExportImpactUpdate(BaseModel):
    destinations: list[str] = Field(default_factory=list, max_length=100)


class ExceptionOverrideRequest(BaseModel):
    reason: str = Field(min_length=10, max_length=4000)


class ExceptionOverrideRejection(BaseModel):
    reason: str = Field(min_length=10, max_length=4000)


async def override_exception_record(
    db, code: str, exception_id: str, user: dict, *, approve: bool, reason: str | None = None
):
    exception = (
        await db.execute(
            select(ExceptionRow)
            .where(ExceptionRow.id == exception_id, ExceptionRow.exception_type == code)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if exception is None:
        raise HTTPException(status_code=404, detail="Audit error occurrence not found")
    case = (
        await db.execute(select(Case).where(Case.id == exception.case_id).with_for_update())
    ).scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    scope.require_store(user, case.store_id)
    if case.status == "Closed" and not approve:
        raise HTTPException(
            status_code=409, detail="Closed cases require a follow-up case for overrides"
        )
    actor = user.get("user") or user.get("sub") or "unknown"
    if approve:
        if exception.status == "Overridden":
            raise HTTPException(status_code=409, detail="Audit error is already overridden")
        if not exception.override_requested_by or exception.override_approved_at:
            raise HTTPException(status_code=409, detail="No pending override request")
        if exception.override_requested_by == actor:
            raise HTTPException(status_code=409, detail="A different Finance approver is required")
        before = {"status": exception.status, "requested_by": exception.override_requested_by}
        exception.status = "Overridden"
        exception.override_approved_by = actor
        exception.override_approved_at = utcnow()
        unresolved = (
            await db.execute(
                select(func.count())
                .select_from(ExceptionRow)
                .where(
                    ExceptionRow.case_id == case.id,
                    ExceptionRow.status.not_in(("Overridden", "Closed", "Resolved")),
                )
            )
        ).scalar_one()
        if unresolved == 0 and case.status != "Closed":
            case.status = "Closed"
            case.investigation_status = "Audit Errors Overridden"
        from integrations.ontology.nimbus import audit

        audit(
            db,
            "SALES_AUDIT_ERROR_OVERRIDE_APPROVED",
            case.id,
            "Exception",
            exception.id,
            f"Sales audit error override approved: {exception.override_reason}",
            actor=actor,
            after={
                "before": before,
                "status": exception.status,
                "requested_by": exception.override_requested_by,
                "approved_by": actor,
                "case_closed": case.status == "Closed",
            },
        )
    else:
        if exception.status != "Open" or exception.override_requested_by:
            raise HTTPException(
                status_code=409, detail="Audit error cannot accept another override request"
            )
        exception.override_reason = reason
        exception.override_requested_by = actor
        exception.override_requested_at = utcnow()
        from integrations.ontology.nimbus import audit

        audit(
            db,
            "SALES_AUDIT_ERROR_OVERRIDE_REQUESTED",
            case.id,
            "Exception",
            exception.id,
            f"Sales audit error override requested: {reason}",
            actor=actor,
            after={"status": exception.status, "reason": reason},
        )
    await db.commit()
    return {
        "id": exception.id,
        "status": exception.status,
        "override_reason": exception.override_reason,
        "override_requested_by": exception.override_requested_by,
        "override_requested_at": exception.override_requested_at,
        "override_approved_by": exception.override_approved_by,
        "override_approved_at": exception.override_approved_at,
        "case_status": case.status,
    }


@router.post("/{code}/exceptions/{exception_id}/override")
async def request_exception_override(
    code: str,
    exception_id: str,
    body: ExceptionOverrideRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("analyst")),
):
    reason = body.reason.strip()
    if len(reason) < 10:
        raise HTTPException(status_code=422, detail="Override rationale must contain 10 characters")
    return await override_exception_record(
        db, code, exception_id, user, approve=False, reason=reason
    )


@router.post("/{code}/exceptions/{exception_id}/override/approve")
async def approve_exception_override(
    code: str,
    exception_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance")),
):
    return await override_exception_record(db, code, exception_id, user, approve=True)


@router.post("/{code}/exceptions/{exception_id}/override/reject")
async def reject_exception_override(
    code: str,
    exception_id: str,
    body: ExceptionOverrideRejection,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance")),
):
    exception = (
        await db.execute(
            select(ExceptionRow)
            .where(ExceptionRow.id == exception_id, ExceptionRow.exception_type == code)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if exception is None:
        raise HTTPException(status_code=404, detail="Audit error occurrence not found")
    case = (
        await db.execute(select(Case).where(Case.id == exception.case_id).with_for_update())
    ).scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    scope.require_store(user, case.store_id)
    if exception.status != "Open" or not exception.override_requested_by:
        raise HTTPException(status_code=409, detail="No pending override request")
    actor = user.get("user") or user.get("sub") or "unknown"
    if exception.override_requested_by == actor:
        raise HTTPException(
            status_code=409, detail="The requester cannot reject their own override"
        )
    rejection_reason = body.reason.strip()
    if len(rejection_reason) < 10:
        raise HTTPException(status_code=422, detail="Rejection reason must contain 10 characters")
    request = {
        "reason": exception.override_reason,
        "requested_by": exception.override_requested_by,
        "requested_at": exception.override_requested_at.isoformat()
        if exception.override_requested_at
        else None,
    }
    exception.override_reason = None
    exception.override_requested_by = None
    exception.override_requested_at = None
    from integrations.ontology.nimbus import audit

    audit(
        db,
        "SALES_AUDIT_ERROR_OVERRIDE_REJECTED",
        case.id,
        "Exception",
        exception.id,
        f"Sales audit error override rejected: {rejection_reason}",
        actor=actor,
        after={"request": request, "rejection_reason": rejection_reason},
    )
    await db.commit()
    return {"id": exception.id, "status": exception.status, "override_requested_by": None}


@router.put("/{code}/export-impact")
async def update_export_impact(
    code: str,
    body: ExportImpactUpdate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("it", "admin")),
):
    """Configure which downstream destinations an active error blocks."""
    await lock_configuration(db, exclusive=True)
    definition = (
        await db.execute(
            select(SalesAuditErrorDefinition).where(SalesAuditErrorDefinition.code == code)
        )
    ).scalar_one_or_none()
    if definition is None:
        raise HTTPException(status_code=404, detail="Sales Audit error definition not found")
    destinations = sorted(set(body.destinations))
    known = set(
        (
            await db.execute(
                select(ExportDestination.name).where(ExportDestination.name.in_(destinations))
            )
        ).scalars()
    )
    if known != set(destinations):
        raise HTTPException(status_code=422, detail="Choose existing export destination names")
    before = {
        "configured": definition.export_impact_configured,
        "destinations": definition.export_block_destinations or [],
    }
    definition.export_impact_configured = True
    definition.export_block_destinations = destinations
    from integrations.ontology.nimbus import audit

    audit(
        db,
        "AUDIT_ERROR_EXPORT_IMPACT_CHANGED",
        None,
        "SalesAuditErrorDefinition",
        code,
        f"Changed export impact for audit error {code}",
        actor=user.get("sub", user.get("username", "unknown")),
        after={
            "before": before,
            "configured": True,
            "destinations": destinations,
        },
    )
    await db.commit()
    return {"code": code, "configured": True, "destinations": destinations}


@router.get("")
async def overview(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role(*ROLES)),
    days: Annotated[int, Query(ge=0, le=3650)] = DEFAULT_WINDOW_DAYS,
    format: Literal["json", "csv"] = "json",
    show: Literal["open", "all"] = "open",
    family: Annotated[str | None, Query(max_length=100)] = None,
    q: Annotated[str | None, Query(max_length=255)] = None,
):
    """One row per error type: the full catalog, with how many are open and how many were raised lately.

    Open counts and amounts cover every open case, however old, since those are the work waiting.
    "Raised" covers cases whose business date falls in the last ``days`` days (0 means all time), so
    the page does not recount every exception ever raised each time it loads.
    """
    since = None if days == 0 else (datetime.now(UTC).date() - timedelta(days=days)).isoformat()
    open_rows = (
        await db.execute(
            scope.restrict(
                select(
                    ExceptionRow.exception_type,
                    Case.currency,
                    func.count(ExceptionRow.id),
                    func.coalesce(func.sum(ExceptionRow.exception_amount), 0),
                    func.coalesce(func.sum(ExceptionRow.estimated_exposure), 0),
                    func.max(ExceptionRow.created_at),
                )
                .join(Case, Case.id == ExceptionRow.case_id)
                .where(OPEN)
                .group_by(ExceptionRow.exception_type, Case.currency),
                user,
                Case.store_id,
            )
        )
    ).all()
    raised_rows = (
        await db.execute(
            scope.restrict(
                select(
                    ExceptionRow.exception_type,
                    func.count(ExceptionRow.id),
                    func.max(ExceptionRow.created_at),
                )
                .join(Case, Case.id == ExceptionRow.case_id)
                .where(*([Case.business_date >= since] if since else []))
                .group_by(ExceptionRow.exception_type),
                user,
                Case.store_id,
            )
        )
    ).all()

    stats: dict[str, dict] = defaultdict(
        lambda: {"open": 0, "total": 0, "latest": None, "by_currency": defaultdict(lambda: [0, 0])}
    )
    for code, currency, count, amount, exposure, latest in open_rows:
        stat = stats[code]
        stat["open"] += count
        stat["latest"] = max(filter(None, [stat["latest"], latest]), default=None)
        by_currency = stat["by_currency"][currency]
        by_currency[0] += Decimal(str(amount))
        by_currency[1] += Decimal(str(exposure))
    for code, count, latest in raised_rows:
        stat = stats[code]
        stat["total"] += count
        stat["latest"] = max(filter(None, [stat["latest"], latest]), default=None)

    catalog = {
        d.code: d for d in (await db.execute(select(SalesAuditErrorDefinition))).scalars().all()
    }
    active_codes = {code for code, definition in catalog.items() if definition.active}
    items = []
    for code in sorted(set(ontology.EXCEPTION_TYPES) | active_codes | set(stats)):
        text, meta, stat = definition_text(catalog.get(code)), ontology_text(code), stats.get(code)
        solution = text["recommended_solution"] or meta["steps"]
        items.append(
            {
                "code": code,
                "label": meta["label"]
                or (catalog[code].short_description if code in catalog else code),
                "family": meta["family"] or text["form"] or "Other",
                **text,
                "recommended_solution": solution,
                "known_causes": meta["known_causes"],
                "resolution": meta["resolution"],
                "open_count": stat["open"] if stat else 0,
                "total_count": stat["total"] if stat else 0,
                "latest_at": stat["latest"].isoformat() if stat and stat["latest"] else None,
                "open_by_currency": [
                    {"currency": currency, "amount": str(amount), "exposure": str(exposure)}
                    for currency, (amount, exposure) in sorted(
                        (stat["by_currency"] if stat else {}).items(), key=lambda kv: str(kv[0])
                    )
                ],
            }
        )
    items.sort(key=lambda i: (-i["open_count"], -i["total_count"], i["code"]))
    if format == "csv":
        normalized_query = (q or "").strip().casefold()
        matching = [
            item
            for item in items
            if (show == "all" or item["open_count"] > 0)
            and (not family or item["family"] == family)
            and (
                not normalized_query
                or normalized_query in item["label"].casefold()
                or normalized_query in item["code"].casefold()
            )
        ]
        columns = (
            "code",
            "label",
            "family",
            "open_count",
            "total_count",
            "latest_at",
            "open_by_currency",
        )
        rows = (
            (
                item["code"],
                item["label"],
                item["family"],
                item["open_count"],
                item["total_count"],
                item["latest_at"],
                "; ".join(
                    f"{amount['currency']} {amount['amount']}"
                    for amount in item["open_by_currency"]
                ),
            )
            for item in matching[:MAX_ERROR_TYPE_EXPORT]
        )
        body = csv_row(columns) + "".join(csv_row(row) for row in rows)
        return Response(
            content=body,
            media_type="text/csv",
            headers={
                "Content-Disposition": 'attachment; filename="audit-error-types.csv"',
                "X-Audit-Error-Types-Truncated": str(len(matching) > MAX_ERROR_TYPE_EXPORT).lower(),
            },
        )
    return {
        "items": items,
        "open_total": sum(i["open_count"] for i in items),
        "open_types": sum(1 for i in items if i["open_count"]),
        "window_days": days,
    }


@router.get("/patterns")
async def patterns(
    days: Annotated[int, Query(ge=7, le=180)] = 30,
    min_days: Annotated[int, Query(ge=2, le=60)] = 3,
    until: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}-\d{2}$")] = None,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role(*ROLES)),
):
    """Error codes that keep coming back at one store or one register."""
    try:
        end = date.fromisoformat(until) if until else datetime.now(UTC).date()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="until must be a valid date") from exc
    catalog = {
        d.code: d.short_description
        for d in (
            await db.execute(
                select(SalesAuditErrorDefinition).where(SalesAuditErrorDefinition.active.is_(True))
            )
        ).scalars()
    }

    def label(code: str) -> dict:
        return {"label": ontology_text(code)["label"] or catalog.get(code) or code}

    return await error_patterns(db, user, end, days, min_days, label)


@router.get("/{code}/cases")
async def cases_for_error(
    code: str,
    limit: Annotated[int, Query(ge=1, le=MAX_CASES)] = 25,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role(*ROLES)),
    status: Annotated[Literal["open", "closed", "all"], Query()] = "open",
    store: Annotated[str | None, Query(max_length=50)] = None,
    date_from: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}-\d{2}$")] = None,
    date_to: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}-\d{2}$")] = None,
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    format: Literal["json", "csv"] = "json",
):
    """One page of the cases holding this error type, newest business date first.

    Filters (status, store, dates) run in the database. There is no total, because counting every
    case that ever held a common error is a scan: ``has_more`` says whether another page follows.
    """
    holds_code = (
        select(ExceptionRow.id)
        .where(ExceptionRow.case_id == Case.id, ExceptionRow.exception_type == code)
        .exists()
    )
    filters = [holds_code]
    if status == "open":
        filters.append(OPEN)
    elif status == "closed":
        filters.append(Case.status == "Closed")
    if store and store.strip():
        filters.append(contains_literal(Case.store_id, store))
    if date_from:
        filters.append(Case.business_date >= date_from)
    if date_to:
        filters.append(Case.business_date <= date_to)
    case_query = scope.restrict(select(Case).where(*filters), user, Case.store_id)
    if format == "csv":
        total = (
            await db.execute(select(func.count()).select_from(case_query.subquery()))
        ).scalar_one()
        if total > MAX_CASE_EXPORT:
            raise HTTPException(
                status_code=413,
                detail=f"Export contains {total:,} cases; limit is {MAX_CASE_EXPORT:,}. Narrow the filters.",
            )
        error_count = (
            select(func.count(ExceptionRow.id))
            .where(ExceptionRow.case_id == Case.id, ExceptionRow.exception_type == code)
            .correlate(Case)
            .scalar_subquery()
        )
        columns = (
            "case_number",
            "status",
            "store_id",
            "business_date",
            "amount",
            "currency",
            "priority",
            "error_count",
        )

        async def generate():
            yield csv_row(columns)
            result = await db.stream(
                scope.restrict(
                    select(Case, error_count.label("error_count")).where(*filters),
                    user,
                    Case.store_id,
                )
                .order_by(Case.business_date.desc(), Case.created_at.desc(), Case.id)
                .execution_options(yield_per=1000)
            )
            try:
                async for case, errors in result:
                    yield csv_row(
                        (
                            case.case_number,
                            case.status,
                            case.store_id,
                            case.business_date,
                            str(case.total_exception_amount),
                            case.currency,
                            case.priority,
                            errors,
                        )
                    )
            finally:
                await result.close()

        return StreamingResponse(
            generate(),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="audit-error-cases.csv"'},
        )
    found = (
        (
            await db.execute(
                case_query.order_by(Case.business_date.desc(), Case.created_at.desc(), Case.id)
                .offset((page - 1) * limit)
                .limit(limit + 1)
            )
        )
        .scalars()
        .all()
    )
    cases = found[:limit]
    # How many of this error each case holds, for just the cases on this page.
    errors = dict(
        (
            await db.execute(
                select(ExceptionRow.case_id, func.count())
                .where(
                    ExceptionRow.case_id.in_([c.id for c in cases]),
                    ExceptionRow.exception_type == code,
                )
                .group_by(ExceptionRow.case_id)
            )
        ).all()
    )
    transaction_ids_by_case: dict[str, list[str]] = defaultdict(list)
    truncated_transaction_cases: set[str] = set()
    if cases:
        creation_events = (
            await db.execute(
                select(AuditEvent.case_id, AuditEvent.after_state)
                .where(
                    AuditEvent.case_id.in_([c.id for c in cases]),
                    AuditEvent.event_type == "CASE_CREATED",
                )
                .order_by(AuditEvent.created_at, AuditEvent.id)
            )
        ).all()
        for case_id, after_state in creation_events:
            state = after_state or {}
            found_ids = state.get("transaction_ids") or (
                [state["transaction_id"]] if state.get("transaction_id") else []
            )
            if not isinstance(found_ids, list):
                continue
            if len(found_ids) > 20:
                truncated_transaction_cases.add(case_id)
            for transaction_id in found_ids[:20]:
                if (
                    isinstance(transaction_id, str)
                    and transaction_id not in transaction_ids_by_case[case_id]
                ):
                    transaction_ids_by_case[case_id].append(transaction_id)
        candidate_ids = sorted(
            {transaction_id for ids in transaction_ids_by_case.values() for transaction_id in ids}
        )
        if candidate_ids:
            case_scope = {c.id: (c.store_id, c.business_date) for c in cases}
            transactions = (
                await db.execute(
                    scope.restrict(
                        select(
                            CanonicalTransaction.id,
                            CanonicalTransaction.store_id,
                            CanonicalTransaction.business_date,
                        ).where(CanonicalTransaction.id.in_(candidate_ids)),
                        user,
                        CanonicalTransaction.store_id,
                    )
                )
            ).all()
            valid_transactions = {(row.id, row.store_id, row.business_date) for row in transactions}
            transaction_ids_by_case = defaultdict(
                list,
                {
                    case_id: [
                        transaction_id
                        for transaction_id in ids
                        if (transaction_id, *case_scope[case_id]) in valid_transactions
                    ][:20]
                    for case_id, ids in transaction_ids_by_case.items()
                },
            )
    return {
        "items": [
            {
                "id": c.id,
                "case_number": c.case_number,
                "status": c.status,
                "store_id": c.store_id,
                "business_date": c.business_date,
                "amount": str(c.total_exception_amount),
                "currency": c.currency,
                "priority": c.priority,
                "errors": errors.get(c.id, 0),
                "transaction_ids": transaction_ids_by_case.get(c.id, []),
                "transactions_truncated": c.id in truncated_transaction_cases,
            }
            for c in cases
        ],
        "page": page,
        "page_size": limit,
        "has_more": len(found) > limit,
    }
