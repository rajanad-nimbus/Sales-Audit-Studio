"""Retail foundation data used by Sales Audit controls."""

import csv
import hashlib
import io
import json
import uuid
from typing import Literal

from database import get_db
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from model_base import utcnow
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.controls.foundation.models import (
    FoundationImportRun,
    FoundationReference,
    StoreLocationTrait,
)
from apps.controls.foundation.tenders import normalize_attributes
from apps.csv_export import csv_row
from apps.database_errors import violates_unique_constraint
from apps.security.authentication import require_role
from apps.store_days.freshness import (
    invalidate_configuration,
    invalidate_configuration_locked,
    lock_configuration,
)

router = APIRouter(prefix="/api/foundation", tags=["Retail foundation data"])
VALID_CATEGORIES = {
    "Store",
    "Register",
    "Cashier",
    "Tender",
    "Item",
    "BankAccount",
    "GLAccount",
    "TaxCode",
    "BusinessCalendar",
}


def store_trait_rows(store_id: str, attributes: dict, active: bool) -> list[StoreLocationTrait]:
    traits = (
        attributes.get("location_traits", []) if active and isinstance(attributes, dict) else []
    )
    return [
        StoreLocationTrait(id=str(uuid.uuid4()), store_id=store_id, location_trait=trait)
        for trait in sorted(set(traits))
    ]


class ReferenceBody(BaseModel):
    category: str = Field(min_length=1, max_length=50)
    code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=255)
    active: bool = True
    attributes: dict = Field(default_factory=dict)

    @field_validator("category", "code", "name", mode="before")
    @classmethod
    def normalize_required_text(cls, value):
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("Value cannot be blank")
        return normalized

    @model_validator(mode="after")
    def validate_tender_attributes(self):
        """Tender attributes are checked the same way here as on the Tender types screen."""
        if self.category == "Tender":
            self.attributes = normalize_attributes(self.attributes)
        return self

    @model_validator(mode="after")
    def normalize_store_location_traits(self):
        """Keep Store traits in the canonical shape consumed by total calculation."""
        if self.category != "Store" or "location_traits" not in self.attributes:
            return self
        traits = self.attributes["location_traits"]
        if not isinstance(traits, list) or len(traits) > 100:
            raise ValueError("Store location_traits must be a list of at most 100 nonblank strings")
        normalized_traits = []
        for trait in traits:
            if not isinstance(trait, str) or not trait.strip():
                raise ValueError("Store location_traits must contain only nonblank strings")
            normalized_traits.append(trait.strip())
        normalized_traits = sorted(set(normalized_traits))
        self.attributes = {**self.attributes, "location_traits": normalized_traits}
        return self

    @model_validator(mode="after")
    def normalize_store_area(self):
        """A store's area is a whole number the area-limited totals compare against."""
        if self.category != "Store" or self.attributes.get("area") in (None, ""):
            return self
        area = self.attributes["area"]
        if isinstance(area, bool) or not (
            isinstance(area, int) or (isinstance(area, str) and area.strip().isdigit())
        ):
            raise ValueError("Store area must be a whole number")
        number = int(area)
        if not 0 <= number <= 99999:
            raise ValueError("Store area must be between 0 and 99999")
        self.attributes = {**self.attributes, "area": number}
        return self


class FoundationImportBody(BaseModel):
    csv_text: str = Field(min_length=1, max_length=2_000_000)
    categories: list[str] = Field(min_length=1, max_length=10)
    file_name: str = Field(min_length=1, max_length=255)
    process_name: str | None = Field(default=None, max_length=100)


class FoundationImportApplyBody(FoundationImportBody):
    preview_token: str = Field(pattern=r"^[a-f0-9]{64}$")
    run_id: str = Field(min_length=36, max_length=36)


def parse_foundation_csv(
    csv_text: str, allowed_categories: set[str]
) -> tuple[list[dict], list[dict]]:
    """Parse a bounded foundation CSV and collect row errors without writing partial data."""
    reader = csv.DictReader(io.StringIO(csv_text))
    headers = reader.fieldnames or []
    normalized_headers = [header.strip().lower() for header in headers]
    required = {"category", "code", "name"}
    supported = required | {"active", "attributes_json"}
    if (
        not headers
        or len(set(normalized_headers)) != len(normalized_headers)
        or not required <= set(normalized_headers)
        or set(normalized_headers) - supported
    ):
        return [], [
            {
                "row": 1,
                "message": "CSV headers must include category, code, name and may include active, attributes_json",
            }
        ]
    reader.fieldnames = normalized_headers
    normalized: list[dict] = []
    errors: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for row_number, raw in enumerate(reader, start=2):
        if not raw or not any(
            value and value.strip() for value in raw.values() if isinstance(value, str)
        ):
            continue
        if None in raw:
            errors.append({"row": row_number, "message": "Row has more values than the header"})
            continue
        attributes_text = raw.get("attributes_json") or ""
        try:
            attributes = json.loads(attributes_text) if attributes_text.strip() else {}
            if not isinstance(attributes, dict):
                raise ValueError("attributes_json must contain a JSON object")
            active_text = (raw.get("active") or "true").strip().lower()
            if active_text not in {"true", "false", "1", "0", "yes", "no"}:
                raise ValueError("active must be true or false")
            candidate = ReferenceBody.model_validate(
                {
                    "category": raw.get("category"),
                    "code": raw.get("code"),
                    "name": raw.get("name"),
                    "active": active_text in {"true", "1", "yes"},
                    "attributes": attributes,
                }
            )
            if candidate.category not in allowed_categories:
                raise ValueError(f"category {candidate.category} is not part of this import")
            if candidate.category in {"Store", "Item"}:
                raise ValueError(f"{candidate.category} records are owned by the Shopify sync")
        except (ValueError, ValidationError) as exc:
            message = (
                "; ".join(
                    f"{'.'.join(str(part) for part in issue['loc'])}: {issue['msg']}"
                    for issue in exc.errors()
                )
                if isinstance(exc, ValidationError)
                else str(exc)
            )
            errors.append({"row": row_number, "message": message})
            continue
        key = (candidate.category, candidate.code)
        if key in seen:
            errors.append(
                {
                    "row": row_number,
                    "message": f"Duplicate {candidate.category} code {candidate.code} in file",
                }
            )
            continue
        seen.add(key)
        normalized.append(candidate.model_dump())
        if len(normalized) > 500:
            errors.append(
                {"row": row_number, "message": "A file may contain at most 500 data rows"}
            )
            break
    if not normalized and not errors:
        errors.append({"row": 1, "message": "CSV contains no data rows"})
    return normalized, errors


async def preview_foundation_rows(db: AsyncSession, rows: list[dict]) -> tuple[list[dict], str]:
    """Describe an import and fingerprint the current values it would change."""
    keys = {(row["category"], row["code"]) for row in rows}
    categories = {category for category, _ in keys}
    codes = {code for _, code in keys}
    existing = (
        (
            await db.execute(
                select(FoundationReference).where(
                    FoundationReference.category.in_(categories),
                    FoundationReference.code.in_(codes),
                )
            )
        )
        .scalars()
        .all()
    )
    by_key = {(row.category, row.code): row for row in existing}
    preview = []
    snapshot = []
    for row in rows:
        found = by_key.get((row["category"], row["code"]))
        snapshot.append(
            {
                "input": row,
                "existing": None
                if found is None
                else {
                    "id": found.id,
                    "name": found.name,
                    "active": found.active,
                    "attributes": found.attributes or {},
                    "updated_at": found.updated_at.isoformat() if found.updated_at else None,
                },
            }
        )
        if found is None:
            action = "Create"
        elif (
            found.name == row["name"]
            and found.active == row["active"]
            and (found.attributes or {}) == row["attributes"]
        ):
            action = "Unchanged"
        else:
            action = "Update"
        preview.append(
            {
                "category": row["category"],
                "code": row["code"],
                "name": row["name"],
                "active": row["active"],
                "action": action,
            }
        )
    token = hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    return preview, token


def view(row: FoundationReference):
    return {
        "id": row.id,
        "category": row.category,
        "code": row.code,
        "name": row.name,
        "active": row.active,
        "attributes": row.attributes,
        "updated_by": row.updated_by,
        "updated_at": row.updated_at,
    }


def _is_duplicate_reference_code(exc: IntegrityError) -> bool:
    return violates_unique_constraint(
        exc,
        constraint_names={"uq_foundation_reference_category_code"},
        sqlite_messages={
            "UNIQUE constraint failed: foundation_references.category, foundation_references.code"
        },
    )


def validate_import_categories(categories: list[str]) -> set[str]:
    selected = set(categories)
    if len(selected) != len(categories) or not selected:
        raise HTTPException(
            status_code=422, detail="Choose one or more distinct foundation categories"
        )
    if selected - VALID_CATEGORIES or selected & {"Store", "Item"}:
        raise HTTPException(
            status_code=422,
            detail="Bulk import supports manually managed foundation categories only",
        )
    return selected


def foundation_import_run_view(run: FoundationImportRun) -> dict:
    return {
        "id": run.id,
        "process_name": run.process_name,
        "file_name": run.file_name,
        "categories": run.categories,
        "status": run.status,
        "rows_total": run.rows_total,
        "created": run.created_count,
        "updated": run.updated_count,
        "unchanged": run.unchanged_count,
        "errors": run.errors or [],
        "error": run.error,
        "uploaded_by": run.uploaded_by,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
    }


@router.post("/import/preview")
async def preview_reference_import(
    body: FoundationImportBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it")),
):
    categories = validate_import_categories(body.categories)
    process_name = (body.process_name or body.file_name).strip()
    file_name = body.file_name.strip()
    if not process_name or not file_name:
        raise HTTPException(status_code=422, detail="Process name and file name cannot be blank")
    rows, errors = parse_foundation_csv(body.csv_text, categories)
    run = FoundationImportRun(
        id=str(uuid.uuid4()),
        process_name=process_name,
        file_name=file_name,
        categories=sorted(categories),
        status="Validation failed" if errors else "Previewed",
        rows_total=len(rows) + len({item["row"] for item in errors if item["row"] > 1}),
        errors=errors or None,
        uploaded_by=user.get("user") or "ontology.user",
        finished_at=utcnow() if errors else None,
    )
    db.add(run)
    if errors:
        await db.commit()
        return {
            "valid": False,
            "errors": errors,
            "rows": [],
            "counts": {},
            "run_id": run.id,
        }
    preview, preview_token = await preview_foundation_rows(db, rows)
    counts = {
        action.lower(): sum(row["action"] == action for row in preview)
        for action in ("Create", "Update", "Unchanged")
    }
    await db.commit()
    return {
        "valid": True,
        "errors": [],
        "rows": preview,
        "counts": counts,
        "preview_token": preview_token,
        "run_id": run.id,
    }


@router.get("/import/runs")
async def list_foundation_import_runs(
    q: str | None = Query(default=None, max_length=100),
    status: str | None = Query(default=None, max_length=32),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=30, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("finance", "it")),
):
    filters = _foundation_import_run_filters(q, status)
    total = await db.scalar(select(func.count()).select_from(FoundationImportRun).where(*filters))
    rows = (
        (
            await db.execute(
                select(FoundationImportRun)
                .where(*filters)
                .order_by(FoundationImportRun.started_at.desc(), FoundationImportRun.id.desc())
                .offset(offset)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [foundation_import_run_view(row) for row in rows],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


def _foundation_import_run_filters(q: str | None, status: str | None) -> list:
    filters = []
    normalized_status = (status or "").strip()
    if normalized_status:
        allowed_statuses = {"Previewed", "Validation failed", "Succeeded", "Stale", "Failed"}
        if normalized_status not in allowed_statuses:
            raise HTTPException(status_code=422, detail="Unsupported import process status")
        filters.append(FoundationImportRun.status == normalized_status)
    normalized_query = (q or "").strip()
    if normalized_query:
        filters.append(
            or_(
                func.lower(FoundationImportRun.process_name).contains(
                    normalized_query.lower(), autoescape=True
                ),
                func.lower(FoundationImportRun.file_name).contains(
                    normalized_query.lower(), autoescape=True
                ),
                func.lower(FoundationImportRun.uploaded_by).contains(
                    normalized_query.lower(), autoescape=True
                ),
                func.lower(FoundationImportRun.id).contains(
                    normalized_query.lower(), autoescape=True
                ),
            )
        )
    return filters


@router.get("/import/runs.csv")
async def export_foundation_import_runs(
    q: str | None = Query(default=None, max_length=100),
    status: str | None = Query(default=None, max_length=32),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("finance", "it")),
):
    filters = _foundation_import_run_filters(q, status)
    total = await db.scalar(select(func.count()).select_from(FoundationImportRun).where(*filters))
    row_limit = 50_000
    columns = (
        "process_id",
        "process_name",
        "file_name",
        "categories",
        "status",
        "rows_total",
        "created",
        "updated",
        "unchanged",
        "issues",
        "error",
        "uploaded_by",
        "started_at",
        "finished_at",
    )

    async def generate():
        yield csv_row(columns)
        result = await db.stream(
            select(FoundationImportRun)
            .where(*filters)
            .order_by(FoundationImportRun.started_at.desc(), FoundationImportRun.id.desc())
            .limit(row_limit)
            .execution_options(yield_per=1000)
        )
        try:
            async for run in result.scalars():
                yield csv_row(
                    (
                        run.id,
                        run.process_name,
                        run.file_name,
                        ", ".join(run.categories or []),
                        run.status,
                        run.rows_total,
                        run.created_count,
                        run.updated_count,
                        run.unchanged_count,
                        json.dumps(run.errors or [], ensure_ascii=False),
                        run.error,
                        run.uploaded_by,
                        run.started_at,
                        run.finished_at,
                    )
                )
        finally:
            await result.close()

    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=foundation-import-runs.csv",
            "X-Foundation-Import-Truncated": "true" if total > row_limit else "false",
        },
    )


@router.post("/import")
async def import_references(
    body: FoundationImportApplyBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it")),
):
    categories = validate_import_categories(body.categories)
    rows, errors = parse_foundation_csv(body.csv_text, categories)
    if errors:
        raise HTTPException(
            status_code=422, detail={"message": "CSV validation failed", "errors": errors}
        )

    await lock_configuration(db, exclusive=True)
    run = await db.get(FoundationImportRun, body.run_id)
    actor = user.get("user") or "ontology.user"
    if run is None or run.uploaded_by != actor:
        raise HTTPException(status_code=404, detail="Foundation import process not found")
    if run.status != "Previewed" or run.categories != sorted(categories):
        raise HTTPException(
            status_code=409,
            detail="This import process is no longer ready to apply. Preview the file again.",
        )
    _current_preview, current_token = await preview_foundation_rows(db, rows)
    if current_token != body.preview_token:
        run.status = "Stale"
        run.error = "Foundation data changed after this preview."
        run.finished_at = utcnow()
        await db.commit()
        raise HTTPException(
            status_code=409,
            detail="Foundation data changed after this preview. Preview the file again before applying it.",
        )
    codes = {row["code"] for row in rows}
    existing = (
        (
            await db.execute(
                select(FoundationReference).where(
                    FoundationReference.category.in_(categories),
                    FoundationReference.code.in_(codes),
                )
            )
        )
        .scalars()
        .all()
    )
    by_key = {(row.category, row.code): row for row in existing}
    created = updated = unchanged = 0
    changed_store_traits: dict[str, list[StoreLocationTrait]] = {}
    for item in rows:
        row = by_key.get((item["category"], item["code"]))
        if row is None:
            row = FoundationReference(
                id=str(uuid.uuid4()),
                category=item["category"],
                code=item["code"],
                name=item["name"],
                active=item["active"],
                attributes=item["attributes"],
                updated_by=actor,
            )
            db.add(row)
            created += 1
            if item["category"] == "Store":
                changed_store_traits[row.code] = store_trait_rows(
                    row.code, item["attributes"], item["active"]
                )
        elif (
            row.name == item["name"]
            and row.active == item["active"]
            and (row.attributes or {}) == item["attributes"]
        ):
            unchanged += 1
        else:
            row.name = item["name"]
            row.active = item["active"]
            row.attributes = item["attributes"]
            row.updated_by = actor
            row.updated_at = utcnow()
            updated += 1
            if row.category == "Store":
                changed_store_traits[row.code] = store_trait_rows(
                    row.code, row.attributes, row.active
                )
    changed_store_ids = sorted(changed_store_traits)
    for offset in range(0, len(changed_store_ids), 500):
        store_ids = changed_store_ids[offset : offset + 500]
        await db.execute(
            delete(StoreLocationTrait).where(StoreLocationTrait.store_id.in_(store_ids))
        )
        for store_id in store_ids:
            db.add_all(changed_store_traits[store_id])
    run.status = "Succeeded"
    run.rows_total = len(rows)
    run.created_count = created
    run.updated_count = updated
    run.unchanged_count = unchanged
    run.finished_at = utcnow()
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        failed_run = await db.get(FoundationImportRun, body.run_id)
        if failed_run is not None:
            failed_run.status = "Failed"
            failed_run.error = "The database rejected one or more reference changes."
            failed_run.finished_at = utcnow()
            await db.commit()
        if _is_duplicate_reference_code(exc):
            raise HTTPException(
                status_code=409,
                detail="A reference changed while the import was being applied. Preview the file again.",
            ) from exc
        raise
    return {"created": created, "updated": updated, "unchanged": unchanged}


@router.get("/location-traits")
async def list_location_traits(
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("finance", "it")),
):
    """Return active store location-trait codes for rule and total authoring."""
    traits: set[str] = set()
    offset = 0
    page_size = 500
    while offset < 50_000:
        rows = (
            (
                await db.execute(
                    select(FoundationReference.attributes)
                    .where(
                        FoundationReference.category == "Store",
                        FoundationReference.active.is_(True),
                    )
                    .order_by(FoundationReference.code, FoundationReference.id)
                    .offset(offset)
                    .limit(page_size)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            break
        for attributes in rows:
            values = attributes.get("location_traits", []) if isinstance(attributes, dict) else []
            if isinstance(values, list):
                traits.update(
                    value.strip() for value in values if isinstance(value, str) and value.strip()
                )
        offset += len(rows)
        if len(rows) < page_size:
            break
    if offset >= 50_000 and await db.scalar(
        select(FoundationReference.id)
        .where(
            FoundationReference.category == "Store",
            FoundationReference.active.is_(True),
        )
        .offset(50_000)
        .limit(1)
    ):
        raise HTTPException(
            status_code=409,
            detail="Location trait catalog exceeds the 50,000-store lookup limit",
        )
    return {"items": sorted(traits)}


@router.get("")
async def list_references(
    category: str | None = Query(default=None, max_length=50),
    categories: str | None = Query(default=None, max_length=500),
    q: str | None = Query(default=None, max_length=100),
    format: Literal["json", "csv"] = "json",
    limit: int = Query(default=200, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("finance", "it")),
):
    """Return a bounded page and category counts for the Foundation Data screen."""
    selected = {value.strip() for value in (categories or "").split(",") if value.strip()}
    if category:
        selected.add(category)
    if selected - VALID_CATEGORIES:
        raise HTTPException(status_code=422, detail="Unsupported foundation category")

    filters = []
    if selected:
        filters.append(FoundationReference.category.in_(selected))
    normalized_query = (q or "").strip()
    if normalized_query:
        filters.append(
            or_(
                func.lower(FoundationReference.code).contains(
                    normalized_query.lower(), autoescape=True
                ),
                func.lower(FoundationReference.name).contains(
                    normalized_query.lower(), autoescape=True
                ),
            )
        )

    if format == "csv":
        columns = ("category", "code", "name", "active", "attributes_json")

        async def generate():
            yield csv_row(columns)
            result = await db.stream(
                select(FoundationReference)
                .where(*filters)
                .order_by(
                    FoundationReference.category,
                    FoundationReference.code,
                    FoundationReference.id,
                )
                .execution_options(yield_per=1000)
            )
            try:
                async for row in result.scalars():
                    yield csv_row(
                        (
                            row.category,
                            row.code,
                            row.name,
                            str(row.active).lower(),
                            json.dumps(row.attributes or {}, ensure_ascii=False, sort_keys=True),
                        )
                    )
            finally:
                await result.close()

        return StreamingResponse(
            generate(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=foundation-data.csv"},
        )

    total = (
        await db.execute(select(func.count()).select_from(FoundationReference).where(*filters))
    ).scalar_one()
    category_counts = dict(
        (
            await db.execute(
                select(FoundationReference.category, func.count()).group_by(
                    FoundationReference.category
                )
            )
        ).all()
    )
    rows = (
        (
            await db.execute(
                select(FoundationReference)
                .where(*filters)
                .order_by(
                    FoundationReference.category, FoundationReference.code, FoundationReference.id
                )
                .offset(offset)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [view(row) for row in rows],
        "total": total,
        "category_counts": category_counts,
    }


@router.post("")
async def create_reference(
    body: ReferenceBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it")),
):
    if body.category not in VALID_CATEGORIES:
        raise HTTPException(
            status_code=422, detail=f"category must be one of {sorted(VALID_CATEGORIES)}"
        )
    if body.category == "Store" and body.active and body.attributes.get("location_traits"):
        await invalidate_configuration(
            db,
            actor=user["user"] or "ontology.user",
            reason="Store location traits changed",
            stores={body.code.strip()},
        )
    row = FoundationReference(
        id=str(uuid.uuid4()),
        category=body.category,
        code=body.code.strip(),
        name=body.name.strip(),
        active=body.active,
        attributes=body.attributes,
        updated_by=user["user"] or "ontology.user",
    )
    db.add(row)
    if body.category == "Store":
        db.add_all(store_trait_rows(row.code, row.attributes, row.active))
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if _is_duplicate_reference_code(exc):
            raise HTTPException(
                status_code=409, detail="Reference code already exists in this category"
            ) from exc
        raise
    await db.refresh(row)
    return view(row)


@router.put("/{reference_id}")
async def update_reference(
    reference_id: str,
    body: ReferenceBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it")),
):
    await lock_configuration(db, exclusive=True)
    row = (
        await db.execute(select(FoundationReference).where(FoundationReference.id == reference_id))
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Foundation reference not found")
    if body.category not in VALID_CATEGORIES:
        raise HTTPException(status_code=422, detail="Unsupported foundation category")
    old_category, old_code = row.category, row.code
    old_traits = row.attributes.get("location_traits") if isinstance(row.attributes, dict) else None
    new_traits = body.attributes.get("location_traits")
    if (row.category == "Store" or body.category == "Store") and (
        row.category != body.category
        or row.code != body.code.strip()
        or row.active != body.active
        or old_traits != new_traits
    ):
        stores = ({row.code} if row.category == "Store" else set()) | (
            {body.code.strip()} if body.category == "Store" else set()
        )
        await invalidate_configuration_locked(
            db,
            actor=user["user"] or "ontology.user",
            reason="Store location traits changed",
            stores=stores,
        )
    row.category, row.code, row.name, row.active, row.attributes, row.updated_by, row.updated_at = (
        body.category,
        body.code.strip(),
        body.name.strip(),
        body.active,
        body.attributes,
        user["user"] or "ontology.user",
        utcnow(),
    )
    if old_category == "Store" or row.category == "Store":
        for store_id in {old_code, row.code}:
            await db.execute(
                delete(StoreLocationTrait).where(StoreLocationTrait.store_id == store_id)
            )
        if row.category == "Store":
            db.add_all(store_trait_rows(row.code, row.attributes, row.active))
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if _is_duplicate_reference_code(exc):
            raise HTTPException(
                status_code=409, detail="Reference code already exists in this category"
            ) from exc
        raise
    await db.refresh(row)
    return view(row)
