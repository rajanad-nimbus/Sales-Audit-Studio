"""Retailer-configurable audit total definitions."""

import hashlib
import json
import uuid
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from apps.security import scope
from apps.database_errors import violates_unique_constraint
from apps.ingest.manifests import validate_business_date
from apps.totals.calculation import (
    calculate_store_day,
    dependencies_for_preview,
    effective_definitions,
    store_location_traits,
)
from apps.totals.currency import single_currency
from apps.store_days.freshness import invalidate_configuration, lock_configuration
from apps.security.authentication import require_role
from database import get_db
from apps.totals.models import AuditTotalDefinition, SalesAuditErrorDefinition
from apps.totals.schemas import TotalDefinitionBody
from integrations.ontology.nimbus import audit
from .domain import serialize_definition, validate_definition

router = APIRouter(prefix="/api/total-definitions", tags=["Audit totals"])


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
        len(references) != 2
        or len({row.name for row in references}) != 2
        or any(row.name == name for row in references)
    ):
        raise HTTPException(
            status_code=422,
            detail="Combined totals require two existing totals other than themselves",
        )
    if body.active and any(not row.active for row in references):
        raise HTTPException(
            status_code=422,
            detail="An active combined total cannot reference an inactive total revision",
        )
    if any(row.level != body.level or row.rollups != body.rollups for row in references):
        raise HTTPException(
            status_code=422, detail="Combined totals must use the same balancing level and roll-ups"
        )


def affected_date_range(
    current: AuditTotalDefinition, replacement: TotalDefinitionBody
) -> tuple[str | None, str | None]:
    """Return the full range whose effective definition can change after a revision."""
    starts = (current.start_date, replacement.start_date)
    ends = (current.end_date, replacement.end_date)
    start = None if None in starts else min(starts)
    end = None if None in ends else max(ends)
    return start, end


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


@router.post("")
async def create_definition(
    body: TotalDefinitionBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance")),
):
    await lock_configuration(db, exclusive=True)
    try:
        validate_definition(body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await validate_combined_references(body, db, name=body.name)
    latest = (
        await db.execute(
            select(func.max(AuditTotalDefinition.revision)).where(
                AuditTotalDefinition.name == body.name
            )
        )
    ).scalar() or 0
    await invalidate_configuration(
        db,
        actor=user["user"] or "ontology.user",
        reason=f"Total definition {body.name} changed",
        start_date=body.start_date,
        end_date=body.end_date,
    )
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
    try:
        validate_definition(body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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
    start_date, end_date = affected_date_range(current, body)
    await invalidate_configuration(
        db,
        actor=user["user"] or "ontology.user",
        reason=f"Total definition {current.name} changed",
        start_date=start_date,
        end_date=end_date,
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
    return {
        "definition": view(definition),
        "store_id": store_id,
        "business_date": business_date,
        "value": sum(dimensions.values(), Decimal("0")),
        "currency": currency,
        "record_count": calculation.definition_counts.get(definition.id),
        "dimensions": [{"dimension": key, "value": value} for key, value in dimensions.items()],
    }
