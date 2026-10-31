"""Create auditable follow-up cases without changing closed case history."""
import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.security import scope
from integrations.ontology.nimbus import audit, sla_due
from models import AuditEvent, Case, Exception as CaseException


async def create_follow_up(db: AsyncSession, case_id: str, user: dict) -> Case:
    # Follow-ups share one lineage. Start at the original case so that opening a
    # later closed follow-up cannot create a parallel case for the same correction.
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
        created = (await db.execute(select(AuditEvent).where(AuditEvent.case_id == case_id,
                    AuditEvent.event_type == "CASE_CREATED"))).scalars().all()
        parent_ids = {parent_id for event in created if isinstance(event.after_state, dict)
                      and isinstance((parent_id := event.after_state.get("parent_case_id")), str)}
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

    events = (await db.execute(select(AuditEvent).where(AuditEvent.case_id == parent.id,
        AuditEvent.event_type.in_(["CASE_CREATED", "CASE_EVIDENCE_STALE", "CASE_FOLLOW_UP_LINKED"])))).scalars().all()
    stale_ids = {event.id for event in events if event.event_type == "CASE_EVIDENCE_STALE"}
    links = [event for event in events if event.event_type == "CASE_FOLLOW_UP_LINKED"]
    handled = {item for event in links for item in (event.after_state or {}).get("stale_event_ids", [])}
    child_ids = {child_id for event in links if isinstance((child_id := (event.after_state or {}).get("follow_up_id")), str)}
    # A later correction may already have invalidated the linked follow-up directly.
    # Count that as handled even if nobody revisited the original case afterwards.
    child_events = (await db.execute(select(AuditEvent.object_id, AuditEvent.after_state).where(
        AuditEvent.case_id.in_(child_ids), AuditEvent.event_type == "CASE_EVIDENCE_STALE",
        AuditEvent.object_type == "TransactionAdjustment"))).all() if child_ids else []
    child_corrections = {adjustment_id for adjustment_id, state in child_events
                         if isinstance(state, dict) and state.get("case_closed") is False}
    handled.update(event.id for event in events if event.event_type == "CASE_EVIDENCE_STALE"
                   and event.object_type == "TransactionAdjustment" and event.object_id in child_corrections)
    pending = sorted(stale_ids - handled)
    children = list((await db.execute(select(Case).where(Case.id.in_(child_ids), Case.status != "Closed")
                    .order_by(Case.id).with_for_update())).scalars().all()) if child_ids else []
    if len(children) > 1:
        raise HTTPException(status_code=409, detail="Multiple active follow-ups require manual investigation")
    if children and not pending:
        return children[0]
    if not pending:
        raise HTTPException(status_code=409, detail="No new correction requires a follow-up")
    if children:
        child = children[0]
        if child.status in {"Resolving", "Pending Validation"}:
            raise HTTPException(status_code=409, detail="Follow-up has an active workflow")
        from apps.corrections.reconciliation import invalidate_case_evidence

        for event in events:
            if event.id in pending:
                await invalidate_case_evidence(db, [child], event.object_id, user["user"])
    else:
        originals = (await db.execute(select(CaseException).where(CaseException.case_id == parent.id))).scalars().all()
        if not originals:
            raise HTTPException(status_code=409, detail="Closed case has no exceptions to review")
        child = Case(id=str(uuid.uuid4()), case_number=f"ZA-{parent.business_date.replace('-', '')}-{uuid.uuid4().hex[:12].upper()}",
                     status="Open", case_type=parent.case_type, business_date=parent.business_date, store_id=parent.store_id,
                     currency=parent.currency, amount_needs_review=True, total_exception_amount=parent.total_exception_amount,
                     total_exposure=parent.total_exposure, investigation_status="Pending", evidence_completeness=0,
                     priority=parent.priority, sla_due_at=sla_due(parent.priority))
        db.add(child)
        await db.flush()
        for original in originals:
            db.add(CaseException(id=str(uuid.uuid4()), case_id=child.id, exception_type=original.exception_type,
                exception_family=original.exception_family, source_system=original.source_system,
                detection_origin=original.detection_origin, detection_rule_id=original.detection_rule_id,
                exception_amount=original.exception_amount, estimated_exposure=original.estimated_exposure,
                severity=original.severity, confidence=original.confidence, close_impact=original.close_impact,
                close_blocker_reason=original.close_blocker_reason, status="Open"))
        transaction_ids = sorted({item for event in events if event.event_type == "CASE_CREATED"
                                  for item in (event.after_state or {}).get("transaction_ids", []) if isinstance(item, str)})
        audit(db, "CASE_CREATED", child.id, "Case", child.id, "Follow-up to a corrected closed case",
              actor=user["user"], after={"parent_case_id": parent.id, "transaction_ids": transaction_ids})
    audit(db, "CASE_FOLLOW_UP_LINKED", parent.id, "Case", child.id, "Correction linked to follow-up case",
          actor=user["user"], after={"follow_up_id": child.id, "stale_event_ids": pending})
    await db.commit()
    await db.refresh(child)
    return child
