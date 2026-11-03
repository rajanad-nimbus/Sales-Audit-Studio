"""Retailer-configurable audit total definitions."""

import hashlib
import json
import uuid
from decimal import Decimal

from database import get_db
from fastapi import APIRouter, Depends, HTTPException
from integrations.ontology.nimbus import audit
from model_base import utcnow
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.database_errors import violates_unique_constraint
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


@router.post("")
async def create_definition(
    body: TotalDefinitionBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance")),
):
    await lock_configuration(db, exclusive=True)
    validate_draft(body)
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
    return {
        "definition": view(definition),
        "store_id": store_id,
        "business_date": business_date,
        "value": sum(dimensions.values(), Decimal("0")),
        "currency": currency,
        "record_count": calculation.definition_counts.get(definition.id),
        "dimensions": [{"dimension": key, "value": value} for key, value in dimensions.items()],
    }
