"""Create auditable follow-up cases without changing closed case history."""
import uuid
from dataclasses import dataclass

from fastapi import HTTPException
from integrations.ontology.nimbus import audit, sla_due
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.audit.models import AuditEvent
from apps.cases.identifiers import generate_case_number
from apps.cases.lineage import (
    audit_json_boolean_is,
    case_creation_parent_ids_query,
    linked_follow_up_child_ids_query,
)
from apps.cases.models import (
    Case,
)
from apps.cases.models import (
    Exception as CaseException,
)
from apps.security import scope

EXCEPTION_COPY_PAGE_SIZE = 500


async def create_follow_up(db: AsyncSession, case_id: str, user: dict) -> Case:
    parent = await lock_root_case(db, case_id, user)
    events = await parent_lineage_events(db, parent.id)
    state = await load_follow_up_state(db, parent.id, events)
    if state.children and not state.pending_event_ids:
        return state.children[0]
    if not state.pending_event_ids:
        raise HTTPException(status_code=409, detail="No new correction requires a follow-up")
    if state.children:
        child = state.children[0]
        await refresh_existing_follow_up(db, child, events, state.pending_event_ids, user)
    else:
        child = await create_child_case(db, parent, user)
    audit(db, "CASE_FOLLOW_UP_LINKED", parent.id, "Case", child.id, "Correction linked to follow-up case",
          actor=user["user"], after={"follow_up_id": child.id, "stale_event_ids": state.pending_event_ids})
    await db.commit()
    await db.refresh(child)
    return child


async def lock_root_case(db: AsyncSession, case_id: str, user: dict) -> Case:
    """Resolve a follow-up lineage to its original case and lock that root."""
    seen: set[str] = set()
    while True:
        if case_id in seen:
            raise HTTPException(status_code=409, detail="Cycle in follow-up case lineage")
        seen.add(case_id)
        current = await db.get(Case, case_id)
        if not current:
            raise HTTPException(status_code=404, detail="Case not found")
        scope.require_store(user, current.store_id)
        if current.status != "Closed" or not current.amount_needs_review:
            raise HTTPException(status_code=409, detail="Only a closed case with stale amounts needs a follow-up")
        parent_ids = set((await db.execute(
            case_creation_parent_ids_query(db, case_id)
        )).scalars().all())
        if len(parent_ids) > 1:
            raise HTTPException(status_code=409, detail="Ambiguous follow-up case lineage")
        if not parent_ids:
            break
        case_id = parent_ids.pop()
    parent = (await db.execute(select(Case).where(Case.id == case_id).with_for_update()
                               .execution_options(populate_existing=True))).scalar_one_or_none()
    if not parent:
        raise HTTPException(status_code=404, detail="Case not found")
    scope.require_store(user, parent.store_id)
    if parent.status != "Closed" or not parent.amount_needs_review:
        raise HTTPException(status_code=409, detail="Only a closed case with stale amounts needs a follow-up")

    return parent


async def parent_lineage_events(db: AsyncSession, parent_id: str) -> list[AuditEvent]:
    """Load invalidation and follow-up events without the potentially large creation payload."""
    return (await db.execute(select(AuditEvent).where(AuditEvent.case_id == parent_id,
        AuditEvent.event_type.in_(["CASE_EVIDENCE_STALE", "CASE_FOLLOW_UP_LINKED"])))).scalars().all()


@dataclass
class FollowUpState:
    pending_event_ids: list[str]
    children: list[Case]


async def load_follow_up_state(db: AsyncSession, parent_id: str, events: list[AuditEvent]) -> FollowUpState:
    """Find stale events not already handled and lock the active child, if any."""
    stale_ids = {event.id for event in events if event.event_type == "CASE_EVIDENCE_STALE"}
    links = [event for event in events if event.event_type == "CASE_FOLLOW_UP_LINKED"]
    handled = {
        event_id
        for event in links
        if isinstance(event.after_state, dict)
        and isinstance(event.after_state.get("stale_event_ids"), list)
        for event_id in event.after_state["stale_event_ids"]
        if isinstance(event_id, str)
    }
    child_ids = linked_follow_up_child_ids_query(db, parent_id)
    # A later correction may already have invalidated the linked follow-up directly.
    # Count that as handled even if nobody revisited the original case afterwards.
    child_corrections = set((await db.execute(select(AuditEvent.object_id).where(
        AuditEvent.case_id.in_(child_ids), AuditEvent.event_type == "CASE_EVIDENCE_STALE",
        AuditEvent.object_type == "TransactionAdjustment",
        audit_json_boolean_is(db, AuditEvent, "case_closed", False),
    ).distinct())).scalars().all())
    handled.update(event.id for event in events if event.event_type == "CASE_EVIDENCE_STALE"
                   and event.object_type == "TransactionAdjustment" and event.object_id in child_corrections)
    pending = sorted(stale_ids - handled)
    children = list((await db.execute(select(Case).where(Case.id.in_(child_ids), Case.status != "Closed")
                    .order_by(Case.id).with_for_update())).scalars().all())
    if len(children) > 1:
        raise HTTPException(status_code=409, detail="Multiple active follow-ups require manual investigation")
    return FollowUpState(pending, children)


async def refresh_existing_follow_up(
    db: AsyncSession, child: Case, events: list[AuditEvent], pending: list[str], user: dict
) -> None:
    """Invalidate existing child evidence for newly stale parent corrections."""
    if child.status in {"Resolving", "Pending Validation"}:
        raise HTTPException(status_code=409, detail="Follow-up has an active workflow")
    from apps.corrections.reconciliation import invalidate_case_evidence

    for event in events:
        if event.id in pending:
            await invalidate_case_evidence(db, [child], event.object_id, user["user"])


async def create_child_case(db: AsyncSession, parent: Case, user: dict) -> Case:
    """Copy exception context to a new case without mutating closed history."""
    first_page = await load_exception_copy_page(db, parent.id, None)
    if not first_page:
        raise HTTPException(status_code=409, detail="Closed case has no exceptions to review")
    child = Case(id=str(uuid.uuid4()), case_number=generate_case_number(parent.business_date),
                 status="Open", case_type=parent.case_type, business_date=parent.business_date, store_id=parent.store_id,
                 currency=parent.currency, amount_needs_review=True, total_exception_amount=parent.total_exception_amount,
                 total_exposure=parent.total_exposure, investigation_status="Pending", evidence_completeness=0,
                 priority=parent.priority, sla_due_at=sla_due(parent.priority))
    db.add(child)
    await db.flush()
    originals = first_page
    while originals:
        for original in originals:
            db.add(CaseException(id=str(uuid.uuid4()), case_id=child.id, exception_type=original.exception_type,
                exception_family=original.exception_family, source_system=original.source_system,
                detection_origin=original.detection_origin, detection_rule_id=original.detection_rule_id,
                exception_amount=original.exception_amount, estimated_exposure=original.estimated_exposure,
                severity=original.severity, confidence=original.confidence, close_impact=original.close_impact,
                close_blocker_reason=original.close_blocker_reason, status="Open"))
        await db.flush()
        originals = await load_exception_copy_page(db, parent.id, originals[-1].id)
    creation_state = (await db.execute(select(AuditEvent.after_state).where(
        AuditEvent.case_id == parent.id, AuditEvent.event_type == "CASE_CREATED"
    ).order_by(AuditEvent.created_at.asc(), AuditEvent.id).limit(1))).scalar_one_or_none()
    raw_transaction_ids = creation_state.get("transaction_ids", []) if isinstance(creation_state, dict) else []
    transaction_ids = sorted({item for item in raw_transaction_ids if isinstance(item, str)}) \
        if isinstance(raw_transaction_ids, list) else []
    audit(db, "CASE_CREATED", child.id, "Case", child.id, "Follow-up to a corrected closed case",
          actor=user["user"], after={"parent_case_id": parent.id, "transaction_ids": transaction_ids})
    return child


async def load_exception_copy_page(
    db: AsyncSession, parent_id: str, last_id: str | None
) -> list[CaseException]:
    """Read a stable, bounded page of immutable parent exception context."""
    query = select(CaseException).where(CaseException.case_id == parent_id)
    if last_id is not None:
        query = query.where(CaseException.id > last_id)
    return list((await db.execute(
        query.order_by(CaseException.id).limit(EXCEPTION_COPY_PAGE_SIZE)
    )).scalars().all())
