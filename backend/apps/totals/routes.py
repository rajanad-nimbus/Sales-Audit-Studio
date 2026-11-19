"""Retailer-configurable audit total definitions."""

import hashlib
import json
import uuid
from decimal import Decimal
from typing import Literal

from database import get_db
from fastapi import APIRouter, Depends, HTTPException
from integrations.ontology.nimbus import audit
from model_base import utcnow
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.database_errors import violates_unique_constraint
from apps.exports.models import ExportDestination
from apps.ingest.manifests import validate_business_date
from apps.security import scope
from apps.security.authentication import require_role
from apps.store_days.freshness import invalidate_configuration, lock_configuration
from apps.totals.calculation import (
    DRAFT_STATUS,
    NOT_LIVE_STATUSES,
    PENDING_APPROVAL_STATUS,
    calculate_store_day,
    dependencies_for_preview,
    effective_definitions,
    store_location_traits,
)
from apps.totals.currency import single_currency
from apps.totals.impact import change_impact
from apps.totals.models import AuditTotalDefinition, SalesAuditErrorDefinition
from apps.totals.schemas import ActivationBody, ReturnBody, TotalDefinitionBody

from .domain import serialize_definition, validate_definition

router = APIRouter(prefix="/api/total-definitions", tags=["Audit totals"])


class TotalDefinitionPackageBody(BaseModel):
    format: Literal["zetsa.audit-total-definitions"]
    version: Literal[1]
    definitions: list[dict] = Field(min_length=1, max_length=500)


class TotalDefinitionPackageApplyBody(TotalDefinitionPackageBody):
    preview_token: str = Field(pattern=r"^[a-f0-9]{64}$")


def view(row: AuditTotalDefinition) -> dict[str, object]:
    return serialize_definition(row)


def definition_fingerprint(configuration: dict) -> str:
    """Hash canonical definition data for an audit event without copying full metadata."""
    serialized = json.dumps(
        configuration,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _is_duplicate_total_revision(exc: IntegrityError) -> bool:
    return violates_unique_constraint(
        exc,
        constraint_names={"ix_audit_total_definitions_name_revision"},
        sqlite_messages={
            "UNIQUE constraint failed: audit_total_definitions.name, audit_total_definitions.revision"
        },
    )


async def validate_combined_references(
    body: TotalDefinitionBody, db: AsyncSession, *, name: str
) -> None:
    if body.calculation_basis != "Combined":
        return
    references = (
        (
            await db.execute(
                select(AuditTotalDefinition).where(
                    AuditTotalDefinition.id.in_(body.combined_total_ids)
                )
            )
        )
        .scalars()
        .all()
    )
    if (
        len(references) != len(body.combined_total_ids)
        or len({row.name for row in references}) != len(body.combined_total_ids)
        or any(row.name == name for row in references)
    ):
        raise HTTPException(
            status_code=422,
            detail="Combined totals require existing, distinct totals other than themselves",
        )
    if body.active and any(not row.active or row.status != "Active" for row in references):
        raise HTTPException(
            status_code=422,
            detail="An active combined total must reference active total revisions",
        )
    rollups = [item.model_dump() for item in body.rollups]
    if any(row.level != body.level or row.rollups != rollups for row in references):
        raise HTTPException(
            status_code=422, detail="Combined totals must use the same balancing level and roll-ups"
        )


async def validate_total_usages(body: TotalDefinitionBody, db: AsyncSession) -> None:
    """Reject export usage bindings that point at missing destinations."""
    identifiers = [usage.destination_id for usage in body.usages]
    if not identifiers:
        return
    existing = set(
        (
            await db.execute(
                select(ExportDestination.id).where(ExportDestination.id.in_(identifiers))
            )
        ).scalars()
    )
    if existing != set(identifiers):
        raise HTTPException(
            status_code=422,
            detail="Every total export usage must reference an existing destination",
        )


async def prepare_total_definition_package(db: AsyncSession, package: TotalDefinitionPackageBody):
    """Resolve environment references and validate the latest revision of each packaged total."""
    errors: list[dict] = []
    latest_source: dict[str, tuple[int, dict]] = {}
    source_ids: dict[str, str] = {}
    for row_number, raw in enumerate(package.definitions, start=1):
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append({"row": row_number, "message": "Total definition name is missing"})
            continue
        identifier = raw.get("id")
        if isinstance(identifier, str) and identifier:
            source_ids[identifier] = name.strip()
        try:
            revision = int(raw.get("revision") or 0)
        except (TypeError, ValueError):
            errors.append({"row": row_number, "message": "Revision must be an integer"})
            continue
        key = name.strip().casefold()
        current = latest_source.get(key)
        if current is None or revision > int(current[1].get("revision") or 0):
            latest_source[key] = (row_number, raw)
        elif revision == int(current[1].get("revision") or 0):
            errors.append(
                {"row": row_number, "message": f"Duplicate packaged revision {revision} for {name}"}
            )

    existing_rows = (
        (
            await db.execute(
                select(AuditTotalDefinition).order_by(
                    AuditTotalDefinition.name, AuditTotalDefinition.revision
                )
            )
        )
        .scalars()
        .all()
    )
    existing_by_name: dict[str, AuditTotalDefinition] = {}
    max_revision: dict[str, int] = {}
    for row in existing_rows:
        key = row.name.casefold()
        max_revision[key] = max(max_revision.get(key, 0), row.revision)
        existing_by_name[key] = row
    destinations = (
        await db.execute(
            select(ExportDestination.id, ExportDestination.name, ExportDestination.enabled)
        )
    ).all()
    destination_by_id = {identifier: name for identifier, name, _enabled in destinations}
    destination_by_name = {
        name.casefold(): identifier for identifier, name, _enabled in destinations
    }

    imported: list[dict] = []
    skipped_protected = 0
    used_names: set[str] = set()
    for key, (row_number, raw) in sorted(latest_source.items(), key=lambda item: item[0]):
        if raw.get("required_by_system") is True:
            skipped_protected += 1
            continue
        current = existing_by_name.get(key)
        if current and current.required_by_system:
            errors.append(
                {
                    "row": row_number,
                    "message": f"{current.name} is system-required in this environment and cannot be replaced",
                }
            )
            continue
        target_name = current.name if current else str(raw["name"]).strip()
        if target_name.casefold() in used_names:
            errors.append(
                {"row": row_number, "message": f"Duplicate target total name {target_name}"}
            )
            continue
        used_names.add(target_name.casefold())
        fields = TotalDefinitionBody.model_fields
        values = {key_name: raw[key_name] for key_name in fields if key_name in raw}
        values.update(
            {
                "name": target_name,
                "revision": None,
                "status": "Worksheet",
                "active": False,
                "required_by_system": False,
                "combined_total_ids": [],
                "usages": [],
            }
        )
        try:
            body = TotalDefinitionBody.model_validate(values)
            validate_draft(body)
        except ValidationError as exc:
            detail = "; ".join(
                f"{'.'.join(str(part) for part in issue['loc'])}: {issue['msg']}"
                for issue in exc.errors()
            )
            errors.append({"row": row_number, "message": detail})
            continue
        except HTTPException as exc:
            errors.append({"row": row_number, "message": str(exc.detail)})
            continue
        imported.append(
            {
                "row": row_number,
                "source_revision": int(raw.get("revision") or 0),
                "name": target_name,
                "source": raw,
                "body": body,
                "id": str(uuid.uuid4()),
                "target_revision": max_revision.get(key, 0) + 1,
                "combined_names": [],
            }
        )

    import_by_name = {entry["name"].casefold(): entry for entry in imported}
    all_target_ids = {key: row.id for key, row in existing_by_name.items()} | {
        key: entry["id"] for key, entry in import_by_name.items()
    }
    combined_views: dict[str, dict] = {
        key: {
            "name": row.name,
            "level": row.level,
            "aggregation": row.aggregation,
            "measure": row.measure,
            "location_traits": row.location_traits or [],
            "parameters": row.parameters or [],
            "rollups": row.rollups or [],
        }
        for key, row in existing_by_name.items()
    }
    combined_views.update(
        {
            key: {
                "name": entry["name"],
                "level": entry["body"].level,
                "aggregation": entry["body"].aggregation,
                "measure": entry["body"].measure,
                "location_traits": entry["body"].location_traits,
                "parameters": [item.model_dump() for item in entry["body"].parameters],
                "rollups": [item.model_dump() for item in entry["body"].rollups],
            }
            for key, entry in import_by_name.items()
        }
    )

    for entry in imported:
        raw = entry["source"]
        body = entry["body"]
        source_names = raw.get("combined_total_names")
        if source_names is None:
            source_ids_for_row = raw.get("combined_total_ids") or []
            if not isinstance(source_ids_for_row, list):
                errors.append(
                    {"row": entry["row"], "message": "Combined total references must be a list"}
                )
                continue
            source_names = [
                source_ids.get(identifier) if isinstance(identifier, str) else None
                for identifier in source_ids_for_row
            ]
        elif not isinstance(source_names, list):
            errors.append({"row": entry["row"], "message": "Combined total names must be a list"})
            continue
        source_component_ids = raw.get("combined_total_ids") or []
        if not isinstance(source_component_ids, list):
            errors.append(
                {"row": entry["row"], "message": "Combined total references must be a list"}
            )
            continue
        normalized_names = [
            name.strip() for name in source_names if isinstance(name, str) and name.strip()
        ]
        if body.calculation_basis == "Combined" and len(normalized_names) != len(
            source_component_ids
        ):
            errors.append(
                {
                    "row": entry["row"],
                    "message": "Combined totals need readable component names in the package",
                }
            )
            continue
        if body.calculation_basis == "Combined" and (
            entry["name"].casefold() in {name.casefold() for name in normalized_names}
            or len({name.casefold() for name in normalized_names}) != len(normalized_names)
        ):
            errors.append(
                {
                    "row": entry["row"],
                    "message": "Combined totals cannot reference themselves or repeat a component",
                }
            )
            continue
        component_ids = []
        for component_name in normalized_names:
            component_key = component_name.casefold()
            identifier = all_target_ids.get(component_key)
            if identifier is None:
                errors.append(
                    {
                        "row": entry["row"],
                        "message": f"Combined component {component_name!r} is missing in the target environment and package",
                    }
                )
                break
            component_ids.append(identifier)
        usage_rows = raw.get("usage_destinations") or []
        source_usages = raw.get("usages") or []
        if not isinstance(usage_rows, list) or not isinstance(source_usages, list):
            errors.append({"row": entry["row"], "message": "Export usages must be lists"})
            continue
        usage_names = [row.get("name") if isinstance(row, dict) else None for row in usage_rows]
        if len(usage_names) != len(source_usages):
            usage_names = [
                destination_by_id.get(row.get("destination_id"))
                for row in source_usages
                if isinstance(row, dict) and isinstance(row.get("destination_id"), str)
            ]
        if len(usage_names) != len(source_usages):
            errors.append(
                {"row": entry["row"], "message": "Every export usage needs a destination"}
            )
            continue
        usage_ids = []
        for index, usage_name in enumerate(usage_names):
            identifier = (
                destination_by_name.get(usage_name.casefold())
                if isinstance(usage_name, str)
                else None
            )
            if identifier is None and index < len(source_usages):
                old_id = (
                    source_usages[index].get("destination_id")
                    if isinstance(source_usages[index], dict)
                    else None
                )
                if isinstance(old_id, str) and old_id in destination_by_id:
                    identifier = old_id
            if identifier is None:
                errors.append(
                    {
                        "row": entry["row"],
                        "message": f"Export destination {usage_name!r} is missing in the target environment",
                    }
                )
                break
            usage_ids.append(identifier)
        if len(component_ids) != len(normalized_names) or len(usage_ids) != len(source_usages):
            continue
        values = body.model_dump()
        values["combined_total_ids"] = component_ids
        values["usages"] = [{"destination_id": identifier} for identifier in usage_ids]
        try:
            body = TotalDefinitionBody.model_validate(values)
            validate_draft(body)
            await validate_total_usages(body, db)
        except ValidationError as exc:
            detail = "; ".join(
                f"{'.'.join(str(part) for part in issue['loc'])}: {issue['msg']}"
                for issue in exc.errors()
            )
            errors.append({"row": entry["row"], "message": detail})
            continue
        except (HTTPException, ValueError) as exc:
            detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
            errors.append({"row": entry["row"], "message": str(detail)})
            continue
        entry["body"] = body
        entry["combined_names"] = normalized_names

    imported_names = {entry["name"].casefold() for entry in imported}
    dependencies = {
        key: [
            next((row.name.casefold() for row in existing_rows if row.id == identifier), "")
            for identifier in (row.combined_total_ids or [])
        ]
        for key, row in existing_by_name.items()
        if key not in imported_names
    }
    dependencies.update(
        {
            entry["name"].casefold(): [name.casefold() for name in entry["combined_names"]]
            for entry in imported
        }
    )
    visiting: set[str] = set()
    visited: set[str] = set()

    def has_cycle(name: str) -> bool:
        if name in visiting:
            return True
        if name in visited:
            return False
        visiting.add(name)
        if any(has_cycle(component) for component in dependencies.get(name, [])):
            return True
        visiting.remove(name)
        visited.add(name)
        return False

    for entry in imported:
        if has_cycle(entry["name"].casefold()):
            errors.append(
                {
                    "row": entry["row"],
                    "message": "Combined totals cannot contain a dependency cycle",
                }
            )
            break

    for entry in imported:
        body = entry["body"]
        if body.calculation_basis != "Combined":
            continue
        for component_name in entry["combined_names"]:
            component = combined_views.get(component_name.casefold())
            if component is None:
                continue
            if (
                component["level"] != body.level
                or component["aggregation"] != body.aggregation
                or component["measure"] != body.measure
                or component["location_traits"] != body.location_traits
                or component["parameters"] != [item.model_dump() for item in body.parameters]
                or component["rollups"] != [item.model_dump() for item in body.rollups]
            ):
                errors.append(
                    {
                        "row": entry["row"],
                        "message": f"Combined component {component_name!r} does not match the total's level, measure, parameters, roll-ups, or location traits",
                    }
                )
                break

    state = {
        "source": [
            {
                "name": entry["name"],
                "revision": entry["source_revision"],
                "body": entry["body"].model_dump(),
            }
            for entry in imported
        ],
        "target_totals": [
            {
                "id": row.id,
                "name": row.name,
                "revision": row.revision,
                "status": row.status,
                "active": row.active,
                "required_by_system": row.required_by_system,
                "configuration": view(row),
            }
            for row in existing_rows
        ],
        "destinations": sorted(
            (
                {"id": identifier, "name": name, "enabled": enabled}
                for identifier, name, enabled in destinations
            ),
            key=lambda row: row["id"],
        ),
    }
    token = hashlib.sha256(
        json.dumps(state, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    return imported, errors, token, skipped_protected


def validate_draft(body: TotalDefinitionBody) -> None:
    """Saves only produce drafts; a revision goes live through its reviewed impact."""
    try:
        validate_definition(body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if body.status != DRAFT_STATUS:
        raise HTTPException(
            status_code=422,
            detail="Save the revision as Worksheet, preview its impact, then activate it",
        )


async def load_draft(
    db: AsyncSession,
    definition_id: str,
    statuses: tuple[str, ...] = NOT_LIVE_STATUSES,
) -> AuditTotalDefinition:
    draft = (
        await db.execute(
            select(AuditTotalDefinition).where(AuditTotalDefinition.id == definition_id)
        )
    ).scalar_one_or_none()
    if not draft:
        raise HTTPException(status_code=404, detail="Total definition not found")
    if draft.status not in statuses:
        detail = {
            (DRAFT_STATUS,): "Only a Worksheet revision can be submitted for approval",
            (PENDING_APPROVAL_STATUS,): "Only a revision pending approval can be decided",
        }.get(statuses, "Only a Worksheet or Pending Approval revision has an impact to preview")
        raise HTTPException(status_code=409, detail=detail)
    return draft


async def require_latest(db: AsyncSession, draft: AuditTotalDefinition) -> None:
    latest = (
        await db.execute(
            select(func.max(AuditTotalDefinition.revision)).where(
                AuditTotalDefinition.name == draft.name
            )
        )
    ).scalar()
    if draft.revision != latest:
        raise HTTPException(
            status_code=409, detail="A newer revision of this total exists; use that one"
        )


def require_unscoped(user: dict) -> None:
    if scope.scoped(user):
        raise HTTPException(
            status_code=403,
            detail="Activating a total affects every store; it needs a user who can see all stores",
        )


async def reviewed_impact(db: AsyncSession, draft: AuditTotalDefinition, user: dict, seen: str):
    """Recalculate the impact and refuse it unless it is exactly the one reviewed."""
    report = await change_impact(db, draft, user)
    if report["fingerprint"] != seen:
        raise HTTPException(
            status_code=409,
            detail="The impact has changed since it was reviewed; preview it again",
        )
    newly_broken = [
        row for row in report["rows"] if row["proposed_error"] and not row["current_error"]
    ]
    if newly_broken:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Activating would break calculation for {len(newly_broken)} store day(s): "
                f"{newly_broken[0]['proposed_error']}"
            ),
        )
    return report


def json_safe(value: object) -> object:
    """Audit state is JSON; keep money exact by storing Decimals as strings."""
    return json.loads(json.dumps(value, default=str))


@router.get("")
async def list_definitions(
    db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("finance", "it"))
):
    return [
        view(row)
        for row in (
            await db.execute(select(AuditTotalDefinition).order_by(AuditTotalDefinition.name))
        )
        .scalars()
        .all()
    ]


@router.get("/package")
async def export_definition_package(
    db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("finance", "it"))
):
    """Export every total revision with readable names for environment-specific references."""
    definitions = (
        (
            await db.execute(
                select(AuditTotalDefinition).order_by(
                    AuditTotalDefinition.name, AuditTotalDefinition.revision
                )
            )
        )
        .scalars()
        .all()
    )
    destinations = (
        await db.execute(
            select(
                ExportDestination.id,
                ExportDestination.name,
                ExportDestination.enabled,
            )
        )
    ).all()
    names_by_id = {row.id: row.name for row in definitions}
    destinations_by_id = {
        destination_id: {"id": destination_id, "name": name, "enabled": enabled}
        for destination_id, name, enabled in destinations
    }

    return {
        "format": "zetsa.audit-total-definitions",
        "version": 1,
        "exported_at": utcnow(),
        "definitions": [
            {
                **view(row),
                "combined_total_names": [
                    names_by_id.get(identifier) for identifier in row.combined_total_ids or []
                ],
                "usage_destinations": [
                    destinations_by_id.get(
                        usage.get("destination_id"),
                        {
                            "id": usage.get("destination_id"),
                            "name": None,
                            "enabled": None,
                        },
                    )
                    for usage in row.usages or []
                ],
            }
            for row in definitions
        ],
    }


@router.post("/package/preview")
async def preview_definition_package(
    package: TotalDefinitionPackageBody,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("finance", "it")),
):
    imported, errors, token, skipped_protected = await prepare_total_definition_package(db, package)
    return {
        "valid": not errors,
        "preview_token": token if not errors else None,
        "errors": errors,
        "counts": {
            "worksheets": len(imported),
            "skipped_protected": skipped_protected,
        },
        "definitions": [
            {
                "row": entry["row"],
                "name": entry["name"],
                "source_revision": entry["source_revision"],
                "target_revision": entry["target_revision"],
                "action": "Create inactive Worksheet",
            }
            for entry in imported
        ],
    }


@router.post("/package/apply")
async def apply_definition_package(
    request: TotalDefinitionPackageApplyBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance")),
):
    await lock_configuration(db, exclusive=True)
    package = TotalDefinitionPackageBody(
        format=request.format,
        version=request.version,
        definitions=request.definitions,
    )
    imported, errors, token, _ = await prepare_total_definition_package(db, package)
    if token != request.preview_token:
        raise HTTPException(
            status_code=409,
            detail="The target configuration changed since preview; review the package again",
        )
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})
    if not imported:
        raise HTTPException(status_code=422, detail="The package contains no importable totals")

    actor = user["user"] or "ontology.user"
    rows = [
        AuditTotalDefinition(
            id=entry["id"],
            revision=entry["target_revision"],
            **entry["body"].model_dump(exclude={"revision"}),
            updated_by=actor,
        )
        for entry in imported
    ]
    db.add_all(rows)
    await db.flush()
    for entry, row in zip(imported, rows, strict=True):
        await validate_combined_references(entry["body"], db, name=row.name)
        audit(
            db,
            "TOTAL_DEFINITION_IMPORTED",
            None,
            "AuditTotalDefinition",
            row.id,
            f"Imported total definition {row.name} revision {row.revision} as a Worksheet",
            actor=actor,
            after={
                "revision": row.revision,
                "configuration_hash": definition_fingerprint(
                    entry["body"].model_dump(exclude={"revision"})
                ),
            },
        )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if _is_duplicate_total_revision(exc):
            raise HTTPException(
                status_code=409,
                detail="A total definition revision changed since preview; review the package again",
            ) from exc
        raise
    return {"imported": [view(row) for row in rows]}


@router.post("")
async def create_definition(
    body: TotalDefinitionBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance")),
):
    await lock_configuration(db, exclusive=True)
    validate_draft(body)
    await validate_total_usages(body, db)
    await validate_combined_references(body, db, name=body.name)
    latest = (
        await db.execute(
            select(func.max(AuditTotalDefinition.revision)).where(
                AuditTotalDefinition.name == body.name
            )
        )
    ).scalar() or 0
    # A draft is not effective, so saving it invalidates no store day.
    row = AuditTotalDefinition(
        id=str(uuid.uuid4()),
        revision=int(latest) + 1,
        **body.model_dump(exclude={"revision"}),
        updated_by=user["user"] or "ontology.user",
    )
    db.add(row)
    audit(
        db,
        "TOTAL_DEFINITION_CREATED",
        None,
        "AuditTotalDefinition",
        row.id,
        f"Created total definition {row.name} revision {row.revision}",
        actor=row.updated_by,
        after={
            "revision": row.revision,
            "configuration_hash": definition_fingerprint(body.model_dump(exclude={"revision"})),
        },
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if _is_duplicate_total_revision(exc):
            raise HTTPException(
                status_code=409, detail="A total definition revision already exists"
            ) from exc
        raise
    return view(row)


@router.get("/{definition_id}/revisions")
async def revision_history(
    definition_id: str,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("finance", "it")),
):
    current = (
        await db.execute(
            select(AuditTotalDefinition).where(AuditTotalDefinition.id == definition_id)
        )
    ).scalar_one_or_none()
    if not current:
        raise HTTPException(status_code=404, detail="Total definition not found")
    rows = (
        (
            await db.execute(
                select(AuditTotalDefinition)
                .where(AuditTotalDefinition.name == current.name)
                .order_by(AuditTotalDefinition.revision)
            )
        )
        .scalars()
        .all()
    )
    return [view(row) for row in rows]


@router.put("/{definition_id}")
async def update_definition(
    definition_id: str,
    body: TotalDefinitionBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance")),
):
    await lock_configuration(db, exclusive=True)
    current = (
        await db.execute(
            select(AuditTotalDefinition).where(AuditTotalDefinition.id == definition_id)
        )
    ).scalar_one_or_none()
    if not current:
        raise HTTPException(status_code=404, detail="Total definition not found")
    validate_draft(body)
    await validate_total_usages(body, db)
    await validate_combined_references(body, db, name=current.name)
    latest = (
        await db.execute(
            select(func.max(AuditTotalDefinition.revision)).where(
                AuditTotalDefinition.name == current.name
            )
        )
    ).scalar() or current.revision
    if current.revision != latest:
        raise HTTPException(
            status_code=409,
            detail="This total definition revision is stale; load the latest revision before updating",
        )
    payload = body.model_dump(exclude={"revision", "name"})
    row = AuditTotalDefinition(
        id=str(uuid.uuid4()),
        name=current.name,
        revision=int(latest) + 1,
        **payload,
        updated_by=user["user"] or "ontology.user",
    )
    db.add(row)
    audit(
        db,
        "TOTAL_DEFINITION_REVISED",
        None,
        "AuditTotalDefinition",
        row.id,
        f"Revised total definition {row.name} from revision {current.revision} to {row.revision}",
        actor=row.updated_by,
        after={
            "previous_definition_id": current.id,
            "previous_revision": current.revision,
            "revision": row.revision,
            "configuration_hash": definition_fingerprint({"name": row.name, **payload}),
        },
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if _is_duplicate_total_revision(exc):
            raise HTTPException(
                status_code=409, detail="A total definition revision already exists"
            ) from exc
        raise
    return view(row)


@router.post("/{definition_id}/impact")
async def impact_definition(
    definition_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it")),
):
    """Preview what activating a draft would change, store day by store day."""
    await lock_configuration(db)
    draft = await load_draft(db, definition_id)
    return await change_impact(db, draft, user)


@router.post("/{definition_id}/submit")
async def submit_definition(
    definition_id: str,
    body: ActivationBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance")),
):
    """The editor sends a draft, with the impact they reviewed, to a second approver."""
    require_unscoped(user)
    await lock_configuration(db, exclusive=True)
    draft = await load_draft(db, definition_id, (DRAFT_STATUS,))
    await require_latest(db, draft)
    report = await reviewed_impact(db, draft, user, body.impact_fingerprint)
    actor = user["user"] or "ontology.user"
    draft.status = PENDING_APPROVAL_STATUS
    draft.submitted_by, draft.submitted_at = actor, utcnow()
    draft.submitted_impact_fingerprint = report["fingerprint"]
    audit(
        db,
        "TOTAL_DEFINITION_SUBMITTED",
        None,
        "AuditTotalDefinition",
        draft.id,
        f"Submitted total definition {draft.name} revision {draft.revision} for approval",
        actor=actor,
        after={
            "revision": draft.revision,
            "impact_fingerprint": report["fingerprint"],
            "impact_summary": json_safe(report["summary"]),
        },
    )
    await db.commit()
    return view(draft)


@router.post("/{definition_id}/return")
async def return_definition(
    definition_id: str,
    body: ReturnBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance")),
):
    """Send a submitted revision back to Worksheet with the reviewer's reason."""
    await lock_configuration(db, exclusive=True)
    draft = await load_draft(db, definition_id, (PENDING_APPROVAL_STATUS,))
    actor = user["user"] or "ontology.user"
    submitted_by = draft.submitted_by
    draft.status = DRAFT_STATUS
    draft.submitted_by = draft.submitted_at = draft.submitted_impact_fingerprint = None
    audit(
        db,
        "TOTAL_DEFINITION_RETURNED",
        None,
        "AuditTotalDefinition",
        draft.id,
        f"Returned total definition {draft.name} revision {draft.revision}: {body.rationale}",
        actor=actor,
        after={"revision": draft.revision, "submitted_by": submitted_by, "reason": body.rationale},
    )
    await db.commit()
    return view(draft)


@router.post("/{definition_id}/activate")
async def activate_definition(
    definition_id: str,
    body: ActivationBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance")),
):
    """A second person makes a submitted revision live, if its impact is what was submitted."""
    require_unscoped(user)
    await lock_configuration(db, exclusive=True)
    draft = await load_draft(db, definition_id, (PENDING_APPROVAL_STATUS,))
    actor = user["user"] or "ontology.user"
    if actor in {draft.submitted_by, draft.updated_by}:
        raise HTTPException(
            status_code=403,
            detail="A different user from the one who edited or submitted it must approve it",
        )
    await require_latest(db, draft)
    if body.impact_fingerprint != draft.submitted_impact_fingerprint:
        raise HTTPException(
            status_code=409,
            detail="This is not the impact that was submitted; preview it again",
        )
    try:
        report = await reviewed_impact(db, draft, user, body.impact_fingerprint)
    except HTTPException as exc:
        if exc.status_code == 409:
            raise HTTPException(
                status_code=409,
                detail="The impact has changed since it was submitted; return it for review",
            ) from exc
        raise
    start, end = report["date_range"]["start"], report["date_range"]["end"]
    await invalidate_configuration(
        db,
        actor=actor,
        reason=f"Total definition {draft.name} revision {draft.revision} activated",
        start_date=start,
        end_date=end,
    )
    draft.status, draft.approved_by, draft.approved_at = "Active", actor, utcnow()
    audit(
        db,
        "TOTAL_DEFINITION_ACTIVATED",
        None,
        "AuditTotalDefinition",
        draft.id,
        f"Activated total definition {draft.name} revision {draft.revision}",
        actor=actor,
        after={
            "revision": draft.revision,
            "edited_by": draft.updated_by,
            "submitted_by": draft.submitted_by,
            "replaced_definition_id": report["replaces_definition_id"],
            "replaced_revision": report["replaces_revision"],
            "impact_fingerprint": report["fingerprint"],
            "impact_summary": json_safe(report["summary"]),
            "impact_truncated": report["truncated"],
            "date_range": report["date_range"],
        },
    )
    await db.commit()
    return view(draft)


@router.get("/errors")
async def list_errors(
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("analyst", "finance", "it", "auditor")),
):
    rows = (
        (
            await db.execute(
                select(SalesAuditErrorDefinition)
                .where(SalesAuditErrorDefinition.active.is_(True))
                .order_by(SalesAuditErrorDefinition.code)
            )
        )
        .scalars()
        .all()
    )
    return [{c.name: getattr(row, c.name) for c in row.__table__.columns} for row in rows]


@router.get("/{definition_id}/preview")
async def preview_definition(
    definition_id: str,
    store_id: str,
    business_date: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it")),
):
    scope.require_store(user, store_id)
    definition = (
        await db.execute(
            select(AuditTotalDefinition).where(AuditTotalDefinition.id == definition_id)
        )
    ).scalar_one_or_none()
    if not definition:
        raise HTTPException(status_code=404, detail="Total definition not found")
    try:
        validate_business_date(business_date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        effective = await effective_definitions(db, business_date, store_id)
        definitions = dependencies_for_preview(definition, effective)
        calculation = await calculate_store_day(db, store_id, business_date, definitions)
        currency = single_currency(calculation.currencies)
        if definition.location_traits and not set(definition.location_traits).intersection(
            await store_location_traits(db, store_id)
        ):
            raise ValueError(f"Total {definition.name} does not apply to store {store_id}")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    dimensions = calculation.configured_totals.get(definition.id, {})
    grouped_dimensions = calculation.configured_rollups.get(definition.id, {})
    preview_dimensions = (
        [
            {"dimension": dimension, "rollup_key": rollup, "value": amount}
            for (dimension, rollup), amount in grouped_dimensions.items()
        ]
        if definition.rollups
        else [
            {"dimension": dimension, "rollup_key": None, "value": amount}
            for dimension, amount in dimensions.items()
        ]
    )
    return {
        "definition": view(definition),
        "store_id": store_id,
        "business_date": business_date,
        "value": sum((row["value"] for row in preview_dimensions), Decimal("0")),
        "currency": currency,
        "record_count": calculation.definition_counts.get(definition.id),
        "dimensions": preview_dimensions,
    }
