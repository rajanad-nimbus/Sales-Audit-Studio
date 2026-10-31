"""Retailer-configurable audit total definitions."""
import uuid
import json
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from apps.security import scope
from apps.ingest.manifests import validate_business_date
from apps.totals.calculation import calculate_store_day, dependencies_for_preview, effective_definitions, store_location_traits
from apps.totals.currency import single_currency
from apps.store_days.freshness import invalidate_configuration, lock_configuration
from auth import require_role
from database import get_db
from models import AuditTotalDefinition, SalesAuditErrorDefinition
from .domain import serialize_definition, validate_definition

router = APIRouter(prefix="/api/total-definitions", tags=["Audit totals"])


class TotalDefinitionBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    aggregation: str = Field(default="Sum", max_length=16)
    measure: str = Field(default="Amount", max_length=16)
    source_system: str | None = Field(default=None, max_length=50)
    transaction_types: list[str] = Field(default_factory=list, max_length=100)
    tender_type: str | None = Field(default=None, max_length=50)
    level: str = Field(default="Store", max_length=24)
    active: bool = True
    revision: int | None = None
    description: str | None = Field(default=None, max_length=5000)
    start_date: str | None = Field(default=None, max_length=10)
    end_date: str | None = Field(default=None, max_length=10)
    category: str = Field(default="Miscellaneous", max_length=50)
    over_short_group: str | None = Field(default=None, max_length=32)
    over_short_operator: str | None = Field(default=None, max_length=8)
    calculation_basis: str = Field(default="Raw", max_length=32)
    status: str = Field(default="Worksheet", max_length=24)
    corresponds_to_rtlog: bool = False
    system_calculated: bool = True
    created_with_wizard: bool = True
    required_by_system: bool = False
    display_group: int = Field(default=0, ge=0, le=2**31 - 1)
    realms: list[dict] = Field(default_factory=list, max_length=100)
    joins: list[dict] = Field(default_factory=list, max_length=100)
    parameters: list[dict] = Field(default_factory=list, max_length=100)
    rollups: list[dict] = Field(default_factory=list, max_length=100)
    restrictions: list[dict] = Field(default_factory=list, max_length=100)
    combined_total_ids: list[str] = Field(default_factory=list, max_length=2)
    combined_operator: str | None = Field(default=None, max_length=1)
    location_traits: list[str] = Field(default_factory=list, max_length=100)
    usages: list[dict] = Field(default_factory=list, max_length=100)
    sql_statement: str | None = Field(default=None, max_length=10000)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Total name cannot be blank")
        return normalized

    @field_validator("location_traits")
    @classmethod
    def validate_location_traits(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or value != value.strip() for value in values) or len(set(values)) != len(values):
            raise ValueError("Location traits must be distinct non-empty codes without surrounding whitespace")
        return values

    @model_validator(mode="after")
    def enforce_metadata_size(self):
        metadata = {
            "transaction_types": self.transaction_types,
            "realms": self.realms,
            "joins": self.joins,
            "parameters": self.parameters,
            "rollups": self.rollups,
            "restrictions": self.restrictions,
            "combined_total_ids": self.combined_total_ids,
            "location_traits": self.location_traits,
            "usages": self.usages,
        }
        if len(json.dumps(metadata, separators=(",", ":"), ensure_ascii=False).encode("utf-8")) > 256 * 1024:
            raise ValueError("Total-definition metadata exceeds the 256 KiB limit")
        return self


def view(row: AuditTotalDefinition) -> dict[str, object]:
    return serialize_definition(row)


async def validate_combined_references(body: TotalDefinitionBody, db: AsyncSession, *, name: str) -> None:
    if body.calculation_basis != "Combined":
        return
    references = (await db.execute(select(AuditTotalDefinition).where(
        AuditTotalDefinition.id.in_(body.combined_total_ids)))).scalars().all()
    if len(references) != 2 or len({row.name for row in references}) != 2 or any(row.name == name for row in references):
        raise HTTPException(status_code=422, detail="Combined totals require two existing totals other than themselves")
    if body.active and any(not row.active for row in references):
        raise HTTPException(status_code=422, detail="An active combined total cannot reference an inactive total revision")
    if any(row.level != body.level or row.rollups != body.rollups for row in references):
        raise HTTPException(status_code=422, detail="Combined totals must use the same balancing level and roll-ups")


def affected_date_range(current: AuditTotalDefinition, replacement: TotalDefinitionBody) -> tuple[str | None, str | None]:
    """Return the full range whose effective definition can change after a revision."""
    starts = (current.start_date, replacement.start_date)
    ends = (current.end_date, replacement.end_date)
    start = None if None in starts else min(starts)
    end = None if None in ends else max(ends)
    return start, end


@router.get("")
async def list_definitions(db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("finance", "it"))): return [view(row) for row in (await db.execute(select(AuditTotalDefinition).order_by(AuditTotalDefinition.name))).scalars().all()]
@router.post("")
async def create_definition(body: TotalDefinitionBody, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance"))):
    await lock_configuration(db, exclusive=True)
    try: validate_definition(body)
    except ValueError as exc: raise HTTPException(status_code=422, detail=str(exc)) from exc
    await validate_combined_references(body, db, name=body.name)
    latest = (await db.execute(select(func.max(AuditTotalDefinition.revision)).where(AuditTotalDefinition.name == body.name))).scalar() or 0
    await invalidate_configuration(db, actor=user["user"] or "ontology.user", reason=f"Total definition {body.name} changed",
                                   start_date=body.start_date, end_date=body.end_date)
    row = AuditTotalDefinition(id=str(uuid.uuid4()), revision=int(latest) + 1, **body.model_dump(exclude={"revision"}), updated_by=user["user"] or "ontology.user")
    db.add(row)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="A total definition revision already exists") from exc
    return view(row)

@router.get("/{definition_id}/revisions")
async def revision_history(definition_id: str, db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("finance", "it"))):
    current = (await db.execute(select(AuditTotalDefinition).where(AuditTotalDefinition.id == definition_id))).scalar_one_or_none()
    if not current:
        raise HTTPException(status_code=404, detail="Total definition not found")
    rows = (await db.execute(select(AuditTotalDefinition).where(AuditTotalDefinition.name == current.name).order_by(AuditTotalDefinition.revision))).scalars().all()
    return [view(row) for row in rows]

@router.put("/{definition_id}")
async def update_definition(definition_id: str, body: TotalDefinitionBody, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance"))):
    await lock_configuration(db, exclusive=True)
    current = (await db.execute(select(AuditTotalDefinition).where(AuditTotalDefinition.id == definition_id))).scalar_one_or_none()
    if not current:
        raise HTTPException(status_code=404, detail="Total definition not found")
    try: validate_definition(body)
    except ValueError as exc: raise HTTPException(status_code=422, detail=str(exc)) from exc
    await validate_combined_references(body, db, name=current.name)
    latest = (await db.execute(select(func.max(AuditTotalDefinition.revision)).where(AuditTotalDefinition.name == current.name))).scalar() or current.revision
    start_date, end_date = affected_date_range(current, body)
    await invalidate_configuration(db, actor=user["user"] or "ontology.user", reason=f"Total definition {current.name} changed",
                                   start_date=start_date, end_date=end_date)
    payload = body.model_dump(exclude={"revision", "name"})
    row = AuditTotalDefinition(id=str(uuid.uuid4()), name=current.name, revision=int(latest) + 1, **payload, updated_by=user["user"] or "ontology.user")
    db.add(row)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="A total definition revision already exists") from exc
    return view(row)

@router.get("/errors")
async def list_errors(db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("analyst", "finance", "it", "auditor"))):
    rows = (await db.execute(select(SalesAuditErrorDefinition).where(SalesAuditErrorDefinition.active.is_(True)).order_by(SalesAuditErrorDefinition.code))).scalars().all()
    return [{c.name: getattr(row, c.name) for c in row.__table__.columns} for row in rows]
@router.get("/{definition_id}/preview")
async def preview_definition(definition_id: str, store_id: str, business_date: str, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance", "it"))):
    scope.require_store(user, store_id)
    definition = (await db.execute(select(AuditTotalDefinition).where(AuditTotalDefinition.id == definition_id))).scalar_one_or_none()
    if not definition: raise HTTPException(status_code=404, detail="Total definition not found")
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
    return {"definition": view(definition), "store_id": store_id, "business_date": business_date,
            "value": sum(dimensions.values(), Decimal("0")), "currency": currency,
            "record_count": calculation.definition_counts.get(definition.id),
            "dimensions": [{"dimension": key, "value": value} for key, value in dimensions.items()]}
