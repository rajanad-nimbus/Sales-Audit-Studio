"""GL cross-reference maintenance and paginated journal previews."""

import hashlib
import json
import math
import uuid
from typing import Annotated, Literal

from database import get_db
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from integrations.ontology.nimbus import audit
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.corrections.effects import effective_amounts
from apps.csv_export import csv_row
from apps.database_errors import violates_unique_constraint
from apps.gl.models import GLCrossReference
from apps.ingest.manifests import validate_business_date
from apps.security import scope
from apps.security.authentication import require_role
from apps.store_days.freshness import lock_configuration
from apps.transactions.models import CanonicalTransaction

router = APIRouter(prefix="/api/gl", tags=["General ledger"])
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500
JOURNAL_EXPORT_PAGE_SIZE = 500


class GLMapBody(BaseModel):
    transaction_type: str = Field(min_length=1, max_length=50)
    tender_type: str | None = Field(default=None, max_length=50)
    debit_account: str = Field(min_length=1, max_length=100)
    credit_account: str = Field(min_length=1, max_length=100)
    active: bool = True

    @field_validator("transaction_type", "debit_account", "credit_account")
    @classmethod
    def require_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Value cannot be blank")
        return value.strip()

    @field_validator("tender_type")
    @classmethod
    def normalize_tender(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class GLMapUpdateBody(GLMapBody):
    reason: str = Field(min_length=10, max_length=4000)

    @field_validator("reason")
    @classmethod
    def require_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A reason is required")
        return value.strip()


class GLMapPackageBody(BaseModel):
    format: Literal["zetsa.gl-cross-references"]
    version: Literal[1]
    mappings: list[dict] = Field(min_length=1, max_length=500)


class GLMapPackageApplyBody(GLMapPackageBody):
    preview_token: str = Field(pattern=r"^[a-f0-9]{64}$")
    reason: str = Field(min_length=10, max_length=4000)

    @field_validator("reason")
    @classmethod
    def require_import_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A reason is required")
        return value.strip()


def view(row: GLCrossReference) -> dict:
    return {
        "id": row.id,
        "transaction_type": row.transaction_type,
        "tender_type": row.tender_type,
        "debit_account": row.debit_account,
        "credit_account": row.credit_account,
        "active": row.active,
        "updated_by": row.updated_by,
        "updated_at": row.updated_at,
    }


def configuration(row: GLCrossReference) -> dict:
    """JSON-safe journal mapping state for audit snapshots."""
    return {
        "transaction_type": row.transaction_type,
        "tender_type": row.tender_type,
        "debit_account": row.debit_account,
        "credit_account": row.credit_account,
        "active": row.active,
    }


def _is_duplicate_cross_reference(exc: IntegrityError) -> bool:
    """Recognize either supported GL mapping uniqueness constraint."""
    return violates_unique_constraint(
        exc,
        constraint_names={"uq_gl_cross_reference", "uq_gl_cross_reference_default_by_type"},
        sqlite_messages={
            "UNIQUE constraint failed: gl_cross_references.transaction_type, gl_cross_references.tender_type",
            "UNIQUE constraint failed: gl_cross_references.transaction_type",
        },
    )


@router.get("/cross-references")
async def list_maps(
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("finance", "it")),
):
    rows = (
        (
            await db.execute(
                select(GLCrossReference).order_by(
                    GLCrossReference.transaction_type, GLCrossReference.tender_type
                )
            )
        )
        .scalars()
        .all()
    )
    return [view(row) for row in rows]


async def prepare_map_package(db: AsyncSession, package: GLMapPackageBody):
    errors: list[dict] = []
    existing_rows = (
        (
            await db.execute(
                select(GLCrossReference).order_by(
                    GLCrossReference.transaction_type, GLCrossReference.tender_type
                )
            )
        )
        .scalars()
        .all()
    )
    existing_by_key = {(row.transaction_type, row.tender_type): row for row in existing_rows}
    prepared: list[dict] = []
    seen: set[tuple[str, str | None]] = set()
    for row_number, raw in enumerate(package.mappings, start=1):
        try:
            body = GLMapBody.model_validate(raw)
        except ValidationError as exc:
            detail = "; ".join(
                f"{'.'.join(str(part) for part in issue['loc'])}: {issue['msg']}"
                for issue in exc.errors()
            )
            errors.append({"row": row_number, "message": detail})
            continue
        key = (body.transaction_type, body.tender_type)
        if key in seen:
            errors.append(
                {
                    "row": row_number,
                    "message": "Duplicate transaction and tender mapping in the package",
                }
            )
            continue
        seen.add(key)
        existing = existing_by_key.get(key)
        state = body.model_dump()
        action = "Create"
        if existing is not None:
            action = "Update" if configuration(existing) != state else "Unchanged"
        prepared.append(
            {
                "row": row_number,
                "body": body,
                "existing": existing,
                "action": action,
            }
        )

    token_state = {
        "mappings": [
            {
                "key": [entry["body"].transaction_type, entry["body"].tender_type],
                "body": entry["body"].model_dump(),
            }
            for entry in prepared
        ],
        "target": [configuration(row) for row in existing_rows],
    }
    token = hashlib.sha256(
        json.dumps(token_state, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return prepared, errors, token


@router.get("/cross-references/package")
async def export_map_package(
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("finance", "it")),
):
    rows = (
        (
            await db.execute(
                select(GLCrossReference).order_by(
                    GLCrossReference.transaction_type, GLCrossReference.tender_type
                )
            )
        )
        .scalars()
        .all()
    )
    return {
        "format": "zetsa.gl-cross-references",
        "version": 1,
        "mappings": [configuration(row) for row in rows],
    }


@router.post("/cross-references/package/preview")
async def preview_map_package(
    package: GLMapPackageBody,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("finance", "it")),
):
    prepared, errors, token = await prepare_map_package(db, package)
    return {
        "valid": not errors,
        "preview_token": token if not errors else None,
        "errors": errors,
        "counts": {
            action.lower(): sum(entry["action"] == action for entry in prepared)
            for action in ("Create", "Update", "Unchanged")
        },
        "mappings": [
            {
                "row": entry["row"],
                **entry["body"].model_dump(),
                "action": entry["action"],
            }
            for entry in prepared
        ],
    }


@router.post("/cross-references/package/apply")
async def apply_map_package(
    request: GLMapPackageApplyBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance")),
):
    await lock_configuration(db, exclusive=True)
    package = GLMapPackageBody(
        format=request.format, version=request.version, mappings=request.mappings
    )
    prepared, errors, token = await prepare_map_package(db, package)
    if token != request.preview_token:
        raise HTTPException(
            status_code=409,
            detail="GL mappings changed after preview. Review the package again before applying it.",
        )
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})
    actor = user["user"] or "ontology.user"
    created = updated = unchanged = 0
    for entry in prepared:
        body: GLMapBody = entry["body"]
        row: GLCrossReference | None = entry["existing"]
        if row is None:
            row = GLCrossReference(
                id=str(uuid.uuid4()),
                **body.model_dump(),
                updated_by=actor,
            )
            db.add(row)
            created += 1
            event_type = "GL_CROSS_REFERENCE_CREATED"
            before = None
        elif entry["action"] == "Update":
            before = configuration(row)
            for field, value in body.model_dump().items():
                setattr(row, field, value)
            row.updated_by = actor
            updated += 1
            event_type = "GL_CROSS_REFERENCE_UPDATED"
        else:
            unchanged += 1
            continue
        audit(
            db,
            event_type,
            None,
            "GLCrossReference",
            row.id,
            f"Imported GL mapping for {row.transaction_type} / {row.tender_type or 'all tenders'}: {request.reason}",
            actor=actor,
            after={
                "before": before,
                "after": configuration(row),
                "reason": request.reason,
                "package_format": request.format,
                "package_version": request.version,
            },
        )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if _is_duplicate_cross_reference(exc):
            raise HTTPException(
                status_code=409,
                detail="A GL mapping changed after preview. Review the package again.",
            ) from exc
        raise
    return {"created": created, "updated": updated, "unchanged": unchanged}


@router.post("/cross-references")
async def create_map(
    body: GLMapBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance")),
):
    await lock_configuration(db, exclusive=True)
    actor = user["user"] or "ontology.user"
    row = GLCrossReference(
        id=str(uuid.uuid4()),
        **body.model_dump(),
        updated_by=actor,
    )
    db.add(row)
    audit(
        db,
        "GL_CROSS_REFERENCE_CREATED",
        None,
        "GLCrossReference",
        row.id,
        f"Created GL mapping for {row.transaction_type} / {row.tender_type or 'all tenders'}",
        actor=actor,
        after=configuration(row),
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if _is_duplicate_cross_reference(exc):
            raise HTTPException(
                status_code=409,
                detail="Cross-reference already exists for this transaction and tender",
            ) from exc
        raise
    return view(row)


@router.put("/cross-references/{mapping_id}")
async def update_map(
    mapping_id: str,
    body: GLMapUpdateBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance")),
):
    await lock_configuration(db, exclusive=True)
    row = (
        await db.execute(
            select(GLCrossReference).where(GLCrossReference.id == mapping_id).with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="GL cross-reference not found")
    before = configuration(row)
    actor = user["user"] or "ontology.user"
    values = body.model_dump(exclude={"reason"})
    for field, value in values.items():
        setattr(row, field, value)
    row.updated_by = actor
    audit(
        db,
        "GL_CROSS_REFERENCE_UPDATED",
        None,
        "GLCrossReference",
        row.id,
        f"Updated GL mapping for {row.transaction_type} / {row.tender_type or 'all tenders'}: {body.reason}",
        actor=actor,
        after={"before": before, "after": configuration(row), "reason": body.reason},
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if _is_duplicate_cross_reference(exc):
            raise HTTPException(
                status_code=409,
                detail="Another GL mapping already uses this transaction and tender",
            ) from exc
        raise
    return view(row)


@router.get("/journal-preview")
async def journal_preview(
    store_id: Annotated[str, Query(min_length=1, max_length=50)],
    business_date: str,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it")),
):
    scope.require_store(user, store_id)
    try:
        validate_business_date(business_date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    conditions = (
        CanonicalTransaction.store_id == store_id,
        CanonicalTransaction.business_date == business_date,
    )
    total = (
        await db.execute(select(func.count()).select_from(CanonicalTransaction).where(*conditions))
    ).scalar() or 0
    pages = max(1, math.ceil(total / page_size))
    if page > pages:
        raise HTTPException(status_code=422, detail=f"page must be between 1 and {pages}")

    rows = (
        (
            await db.execute(
                select(CanonicalTransaction)
                .where(*conditions)
                .order_by(CanonicalTransaction.event_timestamp, CanonicalTransaction.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    amounts = await effective_amounts(db, rows)
    maps = (
        (await db.execute(select(GLCrossReference).where(GLCrossReference.active.is_(True))))
        .scalars()
        .all()
    )
    indexed = {}
    for item in maps:
        key = (item.transaction_type, item.tender_type)
        if key in indexed:
            raise HTTPException(
                status_code=409,
                detail=f"Multiple active GL mappings exist for transaction type {item.transaction_type}",
            )
        indexed[key] = item

    lines, unmapped = [], []
    for transaction in rows:
        mapping = indexed.get(
            (transaction.transaction_type, transaction.tender_type)
        ) or indexed.get((transaction.transaction_type, None))
        if mapping is None:
            unmapped.append(transaction.id)
            continue
        amount = amounts[transaction.id]
        debit, credit = (
            (mapping.debit_account, mapping.credit_account)
            if amount >= 0
            else (mapping.credit_account, mapping.debit_account)
        )
        lines.append(
            {
                "transaction_id": transaction.id,
                "debit_account": debit,
                "credit_account": credit,
                "amount": abs(amount),
                "currency": transaction.currency,
            }
        )

    return {
        "store_id": store_id,
        "business_date": business_date,
        "lines": lines,
        "unmapped_transaction_ids": unmapped,
        "total_transactions": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }


@router.get("/journal-preview/export.csv")
async def export_journal_preview(
    store_id: Annotated[str, Query(min_length=1, max_length=50)],
    business_date: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it")),
):
    """Stream all store-day journal lines, retaining unmapped transactions for reconciliation."""
    scope.require_store(user, store_id)
    try:
        validate_business_date(business_date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    conditions = (
        CanonicalTransaction.store_id == store_id,
        CanonicalTransaction.business_date == business_date,
    )
    maps = (
        (await db.execute(select(GLCrossReference).where(GLCrossReference.active.is_(True))))
        .scalars()
        .all()
    )
    indexed = {}
    for item in maps:
        key = (item.transaction_type, item.tender_type)
        if key in indexed:
            raise HTTPException(
                status_code=409,
                detail=f"Multiple active GL mappings exist for transaction type {item.transaction_type}",
            )
        indexed[key] = item

    async def rows():
        columns = (
            "Transaction ID",
            "Transaction type",
            "Journal status",
            "Debit account",
            "Credit account",
            "Amount",
            "Currency",
        )
        yield csv_row(columns)
        last_timestamp = None
        last_id = None
        while True:
            query = select(CanonicalTransaction).where(*conditions)
            if last_timestamp is not None and last_id is not None:
                query = query.where(
                    (CanonicalTransaction.event_timestamp > last_timestamp)
                    | (
                        (CanonicalTransaction.event_timestamp == last_timestamp)
                        & (CanonicalTransaction.id > last_id)
                    )
                )
            transactions = (
                (
                    await db.execute(
                        query.order_by(
                            CanonicalTransaction.event_timestamp, CanonicalTransaction.id
                        ).limit(JOURNAL_EXPORT_PAGE_SIZE)
                    )
                )
                .scalars()
                .all()
            )
            if not transactions:
                return
            last_timestamp = transactions[-1].event_timestamp
            last_id = transactions[-1].id
            amounts = await effective_amounts(db, transactions)
            for transaction in transactions:
                mapping = indexed.get(
                    (transaction.transaction_type, transaction.tender_type)
                ) or indexed.get((transaction.transaction_type, None))
                amount = amounts[transaction.id]
                if mapping is None:
                    yield csv_row(
                        (
                            transaction.id,
                            transaction.transaction_type,
                            "Unmapped",
                            "",
                            "",
                            abs(amount),
                            transaction.currency,
                        )
                    )
                    continue
                debit, credit = (
                    (mapping.debit_account, mapping.credit_account)
                    if amount >= 0
                    else (mapping.credit_account, mapping.debit_account)
                )
                yield csv_row(
                    (
                        transaction.id,
                        transaction.transaction_type,
                        "Mapped",
                        debit,
                        credit,
                        abs(amount),
                        transaction.currency,
                    )
                )
            await db.commit()

    return StreamingResponse(
        rows(),
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="journal-preview.csv"',
        },
    )
