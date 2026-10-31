"""Case evidence, collaboration, timeline, and source lineage endpoints."""

import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import and_, func, or_, select, true
from sqlalchemy.orm import aliased
from sqlalchemy.ext.asyncio import AsyncSession

from apps.agents import investigation_service
from apps.cases.service import locked_scoped_case
from apps.cases.lineage import case_creation_has_transactions, case_transaction_ids_table
from apps.security import scope
from apps.security.authentication import require_role
from database import get_db
from integrations.ontology import nimbus, ontology
from apps.audit.models import AuditEvent
from apps.transactions.models import CanonicalTransaction
from apps.ingest.models import SourceRecord
from model_base import utcnow
from apps.cases.models import (Case, CaseNote, EvidenceRequest, EvidenceSnapshot,
                                Exception as DBException, Recommendation)
from apps.cases.schemas import EvidenceOverride, EvidenceProvide, EvidenceReject, EvidenceRequestCreate, CaseNoteCreate

router = APIRouter()
MAX_CASE_HISTORY_ITEMS = 200
MAX_EVIDENCE_PAGE_SIZE = 200

@router.get("/api/cases/{case_id}/evidence")
async def case_evidence(case_id: str, db: AsyncSession = Depends(get_db),
                        user: dict = Depends(require_role("analyst", "finance", "it", "auditor")),
                        response: Response = None,
                        skip: Annotated[int, Query(ge=0)] = 0,
                        limit: Annotated[int, Query(ge=1, le=MAX_EVIDENCE_PAGE_SIZE)] = 100):
    case = (await db.execute(scope.restrict(select(Case).where(Case.id == case_id), user, Case.store_id))).scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    total = (await db.execute(select(func.count()).select_from(EvidenceSnapshot)
                              .where(EvidenceSnapshot.case_id == case_id))).scalar_one()
    rows = (await db.execute(select(EvidenceSnapshot).where(EvidenceSnapshot.case_id == case_id)
                             .order_by(EvidenceSnapshot.retrieved_at.desc(), EvidenceSnapshot.id.desc())
                             .offset(skip).limit(limit))).scalars().all()
    if response is not None:
        response.headers["X-Total-Count"] = str(total)
        response.headers["X-Has-More"] = str(skip + len(rows) < total).lower()
    return [{"id": row.id, "evidence_type": row.evidence_type, "status": row.evidence_status,
             "source_system": row.source_system, "retrieved_at": row.retrieved_at,
             "source_record_ids": (row.evidence_data or {}).get("source_record_ids", [])} for row in rows]


@router.get("/api/cases/{case_id}/timeline")
async def case_timeline(case_id: str, db: AsyncSession = Depends(get_db),
                        user: dict = Depends(require_role("analyst", "finance", "it", "auditor"))):
    case = (await db.execute(scope.restrict(select(Case).where(Case.id == case_id), user, Case.store_id))).scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    rows = (await db.execute(select(AuditEvent).where(AuditEvent.case_id == case_id)
                             .order_by(AuditEvent.seq.desc()).limit(200))).scalars().all()
    return [{"id": row.id, "seq": row.seq, "event_type": row.event_type, "actor": row.actor,
             "description": row.action_description, "created_at": row.created_at} for row in rows]


@router.get("/api/cases/{case_id}/collaboration")
async def case_collaboration(case_id: str, db: AsyncSession = Depends(get_db),
                             user: dict = Depends(require_role("analyst", "finance", "it", "auditor"))):
    case = (await db.execute(scope.restrict(select(Case).where(Case.id == case_id), user, Case.store_id))).scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    notes = (await db.execute(select(CaseNote).where(CaseNote.case_id == case_id)
                              .order_by(CaseNote.created_at.desc(), CaseNote.id.desc())
                              .limit(MAX_CASE_HISTORY_ITEMS + 1))).scalars().all()
    notes_truncated = len(notes) > MAX_CASE_HISTORY_ITEMS
    notes = notes[:MAX_CASE_HISTORY_ITEMS]
    requests = (await db.execute(select(EvidenceRequest).where(EvidenceRequest.case_id == case_id)
                                 .order_by(EvidenceRequest.created_at.desc(), EvidenceRequest.id.desc())
                                 .limit(MAX_CASE_HISTORY_ITEMS + 1))).scalars().all()
    requests_truncated = len(requests) > MAX_CASE_HISTORY_ITEMS
    requests = requests[:MAX_CASE_HISTORY_ITEMS]
    return {"notes": [{"id": n.id, "author": n.author, "body": n.body, "created_at": n.created_at} for n in notes],
        "notes_truncated": notes_truncated,
        "evidence_requests_truncated": requests_truncated,
        "evidence_requests": [{"id": r.id, "requirement": r.requirement, "requested_from": r.requested_from, "requested_by": r.requested_by, "status": r.status, "created_at": r.created_at, "response": r.response, "reference": r.reference, "fulfilled_by": r.fulfilled_by, "fulfilled_at": r.fulfilled_at,
        "rejection_reason": r.rejection_reason, "override_reason": r.override_reason, "override_financial_impact": str(r.override_financial_impact) if r.override_financial_impact is not None else None,
        "override_requested_by": r.override_requested_by, "override_requested_at": r.override_requested_at, "override_approved_by": r.override_approved_by, "override_approved_at": r.override_approved_at} for r in requests]}


@router.get("/api/cases/{case_id}/reconciliation")
async def case_reconciliation(case_id: str, db: AsyncSession = Depends(get_db),
                              user: dict = Depends(require_role("analyst", "finance", "it", "auditor")),
                              limit: Annotated[int, Query(ge=1, le=500)] = 200,
                              cursor_timestamp: datetime | None = None, cursor_id: str | None = None):
    """Return the immutable source-record lineage used to create an exception case."""
    if (cursor_timestamp is None) != (cursor_id is None):
        raise HTTPException(status_code=422, detail="cursor_timestamp and cursor_id must be provided together")
    if cursor_timestamp is not None and cursor_timestamp.tzinfo is None:
        raise HTTPException(status_code=422, detail="cursor_timestamp must include a timezone")
    case = (await db.execute(scope.restrict(select(Case).where(Case.id == case_id), user, Case.store_id))).scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    has_linked_transactions = (await db.execute(
        select(case_creation_has_transactions(db, AuditEvent))
        .where(AuditEvent.case_id == case_id, AuditEvent.event_type == "CASE_CREATED")
        .order_by(AuditEvent.created_at.asc(), AuditEvent.id).limit(1)
    )).scalar()
    contextual = not bool(has_linked_transactions)
    if has_linked_transactions:
        created_event = aliased(AuditEvent)
        transaction_ids = case_transaction_ids_table(db, created_event)
        query = (
            select(CanonicalTransaction, SourceRecord)
            .select_from(created_event)
            .join(transaction_ids, true())
            .join(CanonicalTransaction, CanonicalTransaction.id == transaction_ids.c.value)
            .outerjoin(SourceRecord, CanonicalTransaction.source_record_id == SourceRecord.id)
            .where(
                created_event.case_id == case_id,
                created_event.event_type == "CASE_CREATED",
                CanonicalTransaction.store_id == case.store_id,
            )
            .distinct()
        )
    else:
        # Legacy/demo cases cannot claim a causal link; return only relevant context.
        query = select(CanonicalTransaction, SourceRecord).outerjoin(
            SourceRecord, CanonicalTransaction.source_record_id == SourceRecord.id
        ).where(
            CanonicalTransaction.store_id == case.store_id,
            CanonicalTransaction.business_date == case.business_date,
        )
    if cursor_timestamp is not None and cursor_id is not None:
        query = query.where(or_(
            CanonicalTransaction.event_timestamp > cursor_timestamp,
            and_(CanonicalTransaction.event_timestamp == cursor_timestamp,
                 CanonicalTransaction.id > cursor_id),
        ))
    rows = (await db.execute(query.order_by(
        CanonicalTransaction.event_timestamp, CanonicalTransaction.id
    ).limit(limit + 1))).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    records = [{
        "transaction_id": tx.id, "source_system": source.source_system if source else "Unknown",
        "source_record_id": source.source_record_id if source else None,
        "transaction_type": tx.transaction_type, "business_date": tx.business_date,
        "event_timestamp": tx.event_timestamp, "amount": tx.signed_amount, "currency": tx.currency,
        "payment_reference": tx.payment_reference, "settlement_reference": tx.settlement_reference,
        "settlement_date": tx.settlement_date, "reconciliation_status": tx.reconciliation_status,
        "source_lineage": tx.source_lineage,
    } for tx, source in rows]
    next_cursor = None
    if has_more and rows:
        last_transaction = rows[-1][0]
        event_timestamp = last_transaction.event_timestamp
        if event_timestamp.tzinfo is None:
            event_timestamp = event_timestamp.replace(tzinfo=timezone.utc)
        next_cursor = {"event_timestamp": event_timestamp, "transaction_id": last_transaction.id}
    return {"case_id": case_id, "linked": not contextual, "message": (
        "Records recorded by the reconciliation run that created this case."
        if not contextual else "No source links were recorded for this legacy case; showing same-store, same-date context only."
    ), "records": records, "sources": sorted({record["source_system"] for record in records}),
            "has_more": has_more, "next_cursor": next_cursor}


@router.post("/api/cases/{case_id}/notes")
async def add_case_note(case_id: str, body: CaseNoteCreate, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance", "it"))):
    await locked_scoped_case(db, case_id, user)
    note = CaseNote(id=str(uuid.uuid4()), case_id=case_id, author=user["user"] or "ontology.user", body=body.body.strip())
    db.add(note); nimbus.audit(db, "CASE_NOTE_ADDED", case_id, "CaseNote", note.id, "Case note added", actor=note.author); await db.commit()
    return {"id": note.id, "author": note.author, "body": note.body, "created_at": note.created_at}


@router.post("/api/cases/{case_id}/evidence-requests")
async def request_case_evidence(case_id: str, body: EvidenceRequestCreate, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance", "it"))):
    await locked_scoped_case(db, case_id, user)
    req = EvidenceRequest(id=str(uuid.uuid4()), case_id=case_id, requirement=body.requirement.strip(), requested_from=body.requested_from.strip(), requested_by=user["user"] or "ontology.user")
    db.add(req); nimbus.audit(db, "EVIDENCE_REQUESTED", case_id, "EvidenceRequest", req.id, f"Requested {req.requirement} from {req.requested_from}", actor=req.requested_by); await db.commit()
    return {"id": req.id, "status": req.status}


@router.post("/api/cases/{case_id}/evidence/provide")
async def provide_case_evidence(case_id: str, body: EvidenceProvide, db: AsyncSession = Depends(get_db),
                                user: dict = Depends(require_role("finance", "it"))):
    """A person supplies or confirms evidence that no ingested record can provide. It fulfils the matching
    evidence request (or records one on the spot), is audited, and counts toward completeness when the
    requirement belongs to the case's exception type."""
    case = await locked_scoped_case(db, case_id, user)
    if case.status == "Closed":
        raise HTTPException(status_code=409, detail="This case is closed. Evidence cannot be added to it.")
    requirement, note = body.requirement.strip(), body.note.strip()
    if not requirement or not note:
        raise HTTPException(status_code=422, detail="Say which requirement this is for and what was supplied or checked.")
    actor = user["user"] or "ontology.user"
    req = (await db.execute(select(EvidenceRequest).where(
        EvidenceRequest.case_id == case_id, EvidenceRequest.status == "Open",
        func.lower(EvidenceRequest.requirement) == requirement.lower()).limit(1))).scalar_one_or_none()
    if not req:
        req = EvidenceRequest(id=str(uuid.uuid4()), case_id=case_id, requirement=requirement,
                              requested_from="Self-supplied", requested_by=actor)
        db.add(req)
    req.status, req.response, req.reference = "Fulfilled", note, (body.reference or "").strip() or None
    req.fulfilled_by, req.fulfilled_at = actor, utcnow()
    exceptions = (await db.execute(select(DBException).where(DBException.case_id == case_id))).scalars().all()
    od = ontology.get(exceptions[0].exception_type) if exceptions else None
    required = {r.lower() for r in (od["evidence"] if od else ontology.DEFAULT_EVIDENCE)}
    counts = requirement.lower() in required
    nimbus.audit(db, "EVIDENCE_PROVIDED", case_id, "EvidenceRequest", req.id,
                 f"{actor} provided evidence for {requirement}" + ("" if counts else " (not a requirement for this exception type)"),
                 actor=actor, after={"requirement": requirement, "reference": req.reference, "counts_toward_completeness": counts})
    # A case blocked on evidence is re-evaluated at once, deterministically, so supplying the last item moves it on.
    reevaluated = False
    if counts and exceptions and case.status == "Open" and case.investigation_status == "Awaiting Evidence":
        await db.flush()
        await investigation_service.run_investigation(db, case, exceptions, use_agent=False)
        reevaluated = True
    await db.commit()
    return {"id": req.id, "status": req.status, "counts_toward_completeness": counts, "reevaluated": reevaluated,
            "evidence_completeness": case.evidence_completeness, "case_status": case.status}


@router.post("/api/cases/{case_id}/evidence-requests/{request_id}/reject")
async def reject_evidence_request(case_id: str, request_id: str, body: EvidenceReject, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance", "it"))):
    await locked_scoped_case(db, case_id, user)
    req = (await db.execute(select(EvidenceRequest).where(EvidenceRequest.id == request_id, EvidenceRequest.case_id == case_id))).scalar_one_or_none()
    if not req or req.status != "Open": raise HTTPException(status_code=409, detail="Open evidence request not found")
    if not body.rationale.strip(): raise HTTPException(status_code=422, detail="A rejection rationale is required")
    req.status, req.rejection_reason = "Rejected", body.rationale.strip()
    nimbus.audit(db, "EVIDENCE_REQUEST_REJECTED", case_id, "EvidenceRequest", req.id, f"Evidence request rejected: {req.rejection_reason}", actor=user["user"] or "ontology.user")
    await db.commit(); return {"id": req.id, "status": req.status}


async def apply_evidence_override(db, case, req, actor):
    req.status, req.override_approved_by, req.override_approved_at = "Overridden", actor, utcnow()
    case.status, case.investigation_status = "Closed", "Evidence Overridden"
    for old in (await db.execute(select(Recommendation).where(Recommendation.case_id == case.id, Recommendation.status == "Proposed"))).scalars().all(): old.status = "Superseded"
    nimbus.audit(db, "EVIDENCE_OVERRIDE_APPROVED", case.id, "EvidenceRequest", req.id, f"Evidence gap overridden by {actor}: {req.override_reason}", actor=actor, after={"financial_impact": str(req.override_financial_impact)})


@router.post("/api/cases/{case_id}/evidence-requests/{request_id}/override")
async def override_evidence_request(case_id: str, request_id: str, body: EvidenceOverride, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance"))):
    case = await locked_scoped_case(db, case_id, user)
    req = (await db.execute(select(EvidenceRequest).where(EvidenceRequest.id == request_id, EvidenceRequest.case_id == case_id))).scalar_one_or_none()
    if not req or not case or req.status not in ("Open", "Rejected"): raise HTTPException(status_code=409, detail="Open or rejected evidence request not found")
    if case.status in {"Resolving", "Pending Validation", "Closed"}:
        raise HTTPException(status_code=409, detail=f"Evidence override is not allowed while case is {case.status}")
    if not body.rationale.strip() or body.financial_impact < 0: raise HTTPException(status_code=422, detail="A rationale and non-negative financial impact are required")
    actor = user["user"] or "ontology.user"; req.override_reason, req.override_financial_impact, req.override_requested_by, req.override_requested_at = body.rationale.strip(), body.financial_impact, actor, utcnow()
    if body.financial_impact > nimbus.AUTONOMY_LIMIT or (
        body.financial_impact > 0 and case.currency != nimbus.THRESHOLD_CURRENCY
    ):
        req.status = "Override Pending"; nimbus.audit(db, "EVIDENCE_OVERRIDE_REQUESTED", case_id, "EvidenceRequest", req.id, "Evidence override awaiting second Finance approver", actor=actor); await db.commit(); return {"id":req.id,"status":req.status,"requires_second_approver":True}
    await apply_evidence_override(db, case, req, actor); await db.commit(); return {"id":req.id,"status":req.status,"requires_second_approver":False}


@router.post("/api/cases/{case_id}/evidence-requests/{request_id}/override/approve")
async def approve_evidence_override(case_id: str, request_id: str, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance"))):
    case = await locked_scoped_case(db, case_id, user)
    req = (await db.execute(select(EvidenceRequest).where(EvidenceRequest.id == request_id, EvidenceRequest.case_id == case_id))).scalar_one_or_none()
    actor = user["user"] or "ontology.user"
    if not req or not case or req.status != "Override Pending": raise HTTPException(status_code=409, detail="Pending evidence override not found")
    if case.status in {"Resolving", "Pending Validation", "Closed"}:
        raise HTTPException(status_code=409, detail=f"Evidence override is not allowed while case is {case.status}")
    if req.override_requested_by == actor: raise HTTPException(status_code=403, detail="A different Finance approver is required")
    await apply_evidence_override(db, case, req, actor); await db.commit(); return {"id":req.id,"status":req.status}
