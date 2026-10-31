"""Case investigation, workflow execution, and validation endpoints."""

import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.agents import investigation_service
from apps.security import scope
from auth import require_role
from database import get_db
from integrations.ontology import nimbus
from models import (CanonicalTransaction, Case, EvidenceSnapshot, Exception as DBException,
                    PolicyEvaluation, SourceRecord, ValidationObligation, Workflow, utcnow)
from schemas import Recommendation as RecommendationSchema, ValidationRunRequest

router = APIRouter()

@router.post("/api/cases/{case_id}/investigate", response_model=RecommendationSchema)
async def investigate_case(case_id: str, db: AsyncSession = Depends(get_db),
                           _: dict = Depends(require_role("finance", "it"))):
    """Run the Investigation agent for a case."""
    case = (await db.execute(select(Case).where(Case.id == case_id).with_for_update()
                             .execution_options(populate_existing=True))).scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    scope.require_store(_, case.store_id)
    if case.status in ("Resolving", "Pending Validation", "Closed"):
        raise HTTPException(status_code=409, detail=f"Case is {case.status}")
    exceptions = (await db.execute(select(DBException).where(DBException.case_id == case_id))).scalars().all()
    if not exceptions:
        # Evidence snapshots hang off an exception; synthetic volume-test cases have none.
        raise HTTPException(status_code=409, detail="This case has no exceptions to investigate. It is likely a synthetic demo case.")
    rec = await investigation_service.run_investigation(db, case, exceptions)
    await db.commit()
    await db.refresh(rec)
    return rec


@router.get("/api/workflows")
async def list_workflows(case_id: str | None = None, db: AsyncSession = Depends(get_db),
                         user: dict = Depends(require_role("analyst", "finance", "it", "auditor"))):
    query = select(Workflow).join(Case, Case.id == Workflow.case_id)
    if case_id:
        query = query.where(Workflow.case_id == case_id)
    query = scope.restrict(query, user, Case.store_id).order_by(Workflow.created_at.desc()).limit(100)
    rows = (await db.execute(query)).scalars().all()
    return [{"id": w.id, "case_id": w.case_id, "workflow_type": w.workflow_type, "state": w.state,
             "current_step": w.current_step, "created_at": w.created_at} for w in rows]


@router.post("/api/workflows/{workflow_id}/execute")
async def execute_workflow(workflow_id: str, db: AsyncSession = Depends(get_db),
                           _: dict = Depends(require_role("finance", "it"))):
    """Resolution agent executes the approved action and opens a validation obligation."""
    workflow_case_id = (await db.execute(
        select(Workflow.case_id).where(Workflow.id == workflow_id)
    )).scalar_one_or_none()
    if workflow_case_id is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    case = (await db.execute(
        select(Case).where(Case.id == workflow_case_id).with_for_update()
        .execution_options(populate_existing=True)
    )).scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    wf = (await db.execute(
        select(Workflow).where(Workflow.id == workflow_id).with_for_update()
        .execution_options(populate_existing=True)
    )).scalar_one_or_none()
    if wf is None or wf.case_id != case.id:
        raise HTTPException(status_code=404, detail="Workflow not found")
    scope.require_store(_, case.store_id)
    if wf.state != "Pending":
        raise HTTPException(status_code=409, detail=f"Workflow is {wf.state}")
    if case.amount_needs_review:
        raise HTTPException(status_code=409, detail="Case amounts require finance review before workflow execution")
    if case.status != "Resolving":
        raise HTTPException(status_code=409, detail=f"Case is {case.status}; workflow execution is not allowed")
    ob = nimbus.resolve(db, case, wf)
    await db.commit()
    return {"workflow_id": wf.id, "state": wf.state, "validation_id": ob.id}


@router.get("/api/validations")
async def list_validations(case_id: str | None = None, db: AsyncSession = Depends(get_db),
                           user: dict = Depends(require_role("analyst", "finance", "it", "auditor"))):
    query = select(ValidationObligation).join(Case, Case.id == ValidationObligation.case_id)
    if case_id:
        query = query.where(ValidationObligation.case_id == case_id)
    query = scope.restrict(query, user, Case.store_id).order_by(ValidationObligation.created_at.desc()).limit(100)
    rows = (await db.execute(query)).scalars().all()
    return [{"id": v.id, "case_id": v.case_id, "workflow_id": v.workflow_id, "status": v.status,
             "expected_observation": v.expected_observation, "verification_result": v.verification_result,
             "due_window_hours": v.due_window_hours} for v in rows]


@router.post("/api/validations/{validation_id}/run")
async def run_validation(validation_id: str, db: AsyncSession = Depends(get_db),
                         req: ValidationRunRequest = Body(...),
                         _: dict = Depends(require_role("finance", "it"))):
    """Verify a workflow only from source observations received after it was executed."""
    obligation_case_id = (await db.execute(
        select(ValidationObligation.case_id).where(ValidationObligation.id == validation_id)
    )).scalar_one_or_none()
    if obligation_case_id is None:
        raise HTTPException(status_code=404, detail="Validation not found")
    case = (await db.execute(
        select(Case).where(Case.id == obligation_case_id).with_for_update()
        .execution_options(populate_existing=True)
    )).scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    scope.require_store(_, case.store_id)
    wf_id = (await db.execute(
        select(ValidationObligation.workflow_id).where(ValidationObligation.id == validation_id)
    )).scalar_one_or_none()
    if wf_id is None:
        raise HTTPException(status_code=404, detail="Validation not found")
    wf = (await db.execute(
        select(Workflow).where(Workflow.id == wf_id).with_for_update()
        .execution_options(populate_existing=True)
    )).scalar_one_or_none()
    ob = (await db.execute(
        select(ValidationObligation).where(ValidationObligation.id == validation_id).with_for_update()
        .execution_options(populate_existing=True)
    )).scalar_one_or_none()
    if ob is None or wf is None or ob.case_id != case.id or wf.case_id != case.id:
        raise HTTPException(status_code=404, detail="Validation not found")
    if ob.status == "Complete":
        raise HTTPException(status_code=409, detail="Already verified")
    if case.amount_needs_review:
        raise HTTPException(status_code=409, detail="Case amounts require finance review before validation")
    if case.status != "Pending Validation" or wf.state != "Executed":
        raise HTTPException(status_code=409, detail="Case or workflow is no longer awaiting validation")
    rows = (await db.execute(
        select(CanonicalTransaction, SourceRecord)
        .join(SourceRecord, CanonicalTransaction.source_record_id == SourceRecord.id)
        .where(
            CanonicalTransaction.store_id == case.store_id,
            CanonicalTransaction.business_date == case.business_date,
            SourceRecord.id.in_(req.source_record_ids),
            SourceRecord.received_at >= ob.created_at,
        )
    )).all()
    if len(rows) != len(req.source_record_ids):
        raise HTTPException(
            status_code=422,
            detail="Every selected source record must be newly received and belong to this case's store day",
        )
    fresh = [
        {"source_record_id": record.id, "transaction_id": transaction.id,
         "source_system": record.source_system}
        for transaction, record in rows
    ]
    primary = (await db.execute(
        select(DBException).where(DBException.case_id == case.id).order_by(DBException.created_at).limit(1)
    )).scalar_one_or_none()
    db.add(EvidenceSnapshot(
        id=str(uuid.uuid4()), case_id=case.id, exception_id=primary.id if primary else "",
        evidence_type="Validation observation", evidence_status="Complete",
        source_system=fresh[0]["source_system"],
        evidence_data={"source_record_ids": [item["source_record_id"] for item in fresh],
                       "validation_id": ob.id, "rationale": req.rationale},
        retrieved_at=utcnow(),
    ))
    nimbus.validate(
        db,
        case,
        wf,
        ob,
        fresh,
        rationale=req.rationale,
        actor=_.get("user") or "ontology.user",
    )
    await db.commit()
    return {"status": ob.status, "verification_result": ob.verification_result, "case_status": case.status}


@router.get("/api/validations/{validation_id}/candidates")
async def validation_candidates(
    validation_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it")),
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    cursor_received_at: datetime | None = None,
    cursor_id: Annotated[str | None, Query(min_length=1, max_length=36)] = None,
):
    """List bounded, post-execution records a reviewer may cite for validation."""
    if (cursor_received_at is None) != (cursor_id is None):
        raise HTTPException(status_code=422, detail="cursor_received_at and cursor_id must be provided together")
    if cursor_received_at is not None and cursor_received_at.tzinfo is None:
        raise HTTPException(status_code=422, detail="cursor_received_at must include a timezone")
    context = (await db.execute(
        select(ValidationObligation, Case)
        .join(Case, Case.id == ValidationObligation.case_id)
        .where(ValidationObligation.id == validation_id)
    )).first()
    if context is None:
        raise HTTPException(status_code=404, detail="Validation not found")
    obligation, case = context
    scope.require_store(user, case.store_id)

    query = (select(CanonicalTransaction, SourceRecord)
             .join(SourceRecord, CanonicalTransaction.source_record_id == SourceRecord.id)
             .where(CanonicalTransaction.store_id == case.store_id,
                    CanonicalTransaction.business_date == case.business_date,
                    SourceRecord.received_at >= obligation.created_at))
    if cursor_received_at is not None and cursor_id is not None:
        query = query.where(or_(
            SourceRecord.received_at > cursor_received_at,
            and_(SourceRecord.received_at == cursor_received_at, SourceRecord.id > cursor_id),
        ))
    rows = (await db.execute(query.order_by(SourceRecord.received_at, SourceRecord.id).limit(limit + 1))).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    candidates = [{
        "source_record_id": source.id,
        "transaction_id": transaction.id,
        "source_system": source.source_system,
        "transaction_type": transaction.transaction_type,
        "amount": transaction.signed_amount,
        "currency": transaction.currency,
        "received_at": source.received_at,
        "payment_reference": transaction.payment_reference,
        "settlement_reference": transaction.settlement_reference,
    } for transaction, source in rows]
    next_cursor = None
    if has_more and rows:
        timestamp = rows[-1][1].received_at
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        next_cursor = {"received_at": timestamp, "source_record_id": rows[-1][1].id}
    return {"items": candidates, "has_more": has_more, "next_cursor": next_cursor}


@router.get("/api/policy-evaluations")
async def list_policy_evaluations(case_id: str | None = None, db: AsyncSession = Depends(get_db),
                                  user: dict = Depends(require_role("analyst", "finance", "it", "auditor"))):
    query = select(PolicyEvaluation).join(Case, Case.id == PolicyEvaluation.case_id)
    if case_id:
        query = query.where(PolicyEvaluation.case_id == case_id)
    query = scope.restrict(query, user, Case.store_id).order_by(PolicyEvaluation.created_at.desc()).limit(100)
    rows = (await db.execute(query)).scalars().all()
    return [{"id": p.id, "case_id": p.case_id, "rule_version": p.rule_version, "outcome": p.outcome,
             "reasons": p.reasons, "created_at": p.created_at} for p in rows]
