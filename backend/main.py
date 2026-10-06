import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

import access
import scope
from database import engine, get_db
import investigation_service
import llm
import nimbus
import ontology
from ontology_integration import integration as ontology_integration
from auth import AUTH_ENABLED, current_user, require_role
from routes_ingest import router as ingest_router
from action_center import router as action_router
from batch import router as batch_router
from audit_api import router as audit_router
from exports import router as exports_router
from routes_store_days import router as store_day_router
from routes_foundation import router as foundation_router
from routes_cash import router as cash_router
from routes_corrections import router as corrections_router
from routes_transactions import router as transactions_router
from routes_gl import router as gl_router
from routes_totals import router as totals_router
from routes_rules import router as rules_router
from routes_operations import router as operations_router
from routes_shopify import router as shopify_router
from routes_agent import router as agent_router
from models import (Base, Case, Connector, PolicyEvaluation, Workflow, ValidationObligation,
                    Exception as DBException, Finding, Recommendation, HumanDecision, AuditEvent,
                    CanonicalTransaction, SourceRecord, EvidenceSnapshot, CaseNote, EvidenceRequest, utcnow)
from schemas import (
    CaseCreate, CaseUpdate, Case as CaseSchema, ExceptionCreate, Exception as ExceptionSchema,
    FindingCreate, Finding as FindingSchema, RecommendationCreate, Recommendation as RecommendationSchema,
    HumanDecisionCreate, HumanDecision as HumanDecisionSchema, CaseDetailResponse, AuditEventSchema,
    ValidationRunRequest, CaseNoteCreate, EvidenceRequestCreate, EvidenceProvide, EvidenceReject, EvidenceOverride
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await ontology_integration.refresh()
    yield
    await engine.dispose()


app = FastAPI(
    title="Nimbus Sales Audit API",
    version="1.0.0",
    description="Agentic Financial Exception Management for Retail Commerce",
    lifespan=lifespan
)

cors_origins_env = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:3000,http://localhost:3001,http://127.0.0.1:3000,http://127.0.0.1:3001",
)
cors_origins = [origin.strip() for origin in cors_origins_env.split(",") if origin.strip()]

app.include_router(ingest_router)
app.include_router(action_router)
app.include_router(batch_router)
app.include_router(audit_router)
app.include_router(exports_router)
app.include_router(store_day_router)
app.include_router(foundation_router)
app.include_router(cash_router)
app.include_router(corrections_router)
app.include_router(transactions_router)
app.include_router(gl_router)
app.include_router(totals_router)
app.include_router(rules_router)
app.include_router(operations_router)
app.include_router(shopify_router)
app.include_router(agent_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def ontology_authentication(request: Request, call_next):
    """Require an Ontology Studio identity for every Nimbus API operation."""
    if (not request.url.path.startswith("/api/") or request.url.path in {"/api/health", "/api/exports/_sink"}
            or request.method == "OPTIONS"):
        return await call_next(request)
    try:
        request.state.user = await current_user(request, request.headers.get("authorization"))
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers or {})
    # Signed in is not enough: the role must be allowed the screen this API belongs to.
    allowed, screens = access.api_allowed(request.state.user["role"], request.url.path)
    if not allowed:
        return JSONResponse(status_code=403, content={"detail": f"Your role ({access.ROLE_LABELS.get(request.state.user['role'], request.state.user['role'])}) "
                                                               f"cannot use {' or '.join(screens)}."})
    # Row-level security: a scoped user (a store manager) may only address their assigned stores.
    store = scope.path_store(request.url.path)
    if store is not None and not scope.store_allowed(request.state.user, store):
        return JSONResponse(status_code=403, content={"detail": f"You are not assigned to store {store}."})
    return await call_next(request)


@app.get("/")
async def root():
    return {
        "message": "Nimbus Sales Audit API",
        "version": "1.0.0",
        "description": "Agentic Financial Exception Management for Retail Commerce"
    }


@app.get("/api/health")
async def health_check():
    return {"status": "healthy", "auth_enabled": AUTH_ENABLED, "ontology": ontology.ONTOLOGY_VERSION,
            "ontology_integration": ontology_integration.status()}


@app.get("/api/production-readiness")
async def production_readiness(db: AsyncSession = Depends(get_db)):
    """Show the concrete prerequisites before a sales-audit cutover."""
    rows = (await db.execute(select(SourceRecord.source_system, func.max(SourceRecord.received_at)).group_by(
        SourceRecord.source_system))).all()
    received = {source: timestamp for source, timestamp in rows}
    sources = [{"source_system": source, "received_at": received.get(source), "ready": source in received}
               for source in ("POS", "POSControl", "Processor", "Bank", "ERP", "Shopify")]
    integration = ontology_integration.status()
    checks = [
        {"name": "Ontology user authentication", "ready": AUTH_ENABLED,
         "detail": "Nimbus delegates user authentication to Ontology Studio."},
        {"name": "Approved Sales Audit ontology", "ready": integration["ready"],
         "detail": "Approve and release the rules, then configure an ons_ integration key."},
        {"name": "Required source feeds", "ready": all(item["ready"] for item in sources),
         "detail": "Configure scheduled read-only POS, control, processor, bank, and ERP deliveries."},
    ]
    return {"ready": all(check["ready"] for check in checks), "checks": checks, "sources": sources,
            "next_inputs": ["source schemas and read-only endpoints", "matching keys and control totals",
                            "cutoff calendar and tolerance policy", "approval matrix and escalation owners"]}


# ============= CASES ENDPOINTS =============

@app.get("/api/cases", response_model=list[CaseSchema])
async def list_cases(skip: int = 0, limit: int = 50, db: AsyncSession = Depends(get_db)):
    """Get all cases with pagination."""
    query = select(Case).offset(skip).limit(limit).order_by(Case.created_at.desc())
    result = await db.execute(query)
    return result.scalars().all()


@app.get("/api/cases/status/{status}", response_model=list[CaseSchema])
async def list_cases_by_status(status: str, db: AsyncSession = Depends(get_db)):
    """Get cases filtered by status."""
    query = select(Case).where(Case.status == status).order_by(Case.updated_at.desc()).limit(100)
    result = await db.execute(query)
    return result.scalars().all()


@app.get("/api/cases/count")
async def count_cases(db: AsyncSession = Depends(get_db)):
    """Get total case count."""
    query = select(func.count(Case.id))
    result = await db.execute(query)
    return {"count": result.scalar()}


@app.get("/api/cases/stats")
async def case_stats(db: AsyncSession = Depends(get_db)):
    """Get case statistics by status."""
    statuses = ["Open", "In Investigation", "In Review", "Closed"]
    stats = {}

    for status in statuses:
        query = select(func.count(Case.id)).where(Case.status == status)
        result = await db.execute(query)
        stats[status] = result.scalar()

    return {
        "by_status": stats,
        "total": sum(stats.values())
    }


@app.get("/api/cases/{case_id}", response_model=CaseDetailResponse)
async def get_case_detail(case_id: str, db: AsyncSession = Depends(get_db)):
    """Get detailed case information with all related entities."""
    # Get case
    case_query = select(Case).where(Case.id == case_id)
    case_result = await db.execute(case_query)
    case = case_result.scalar_one_or_none()

    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    # Get exceptions
    exceptions_query = select(DBException).where(DBException.case_id == case_id)
    exceptions_result = await db.execute(exceptions_query)
    exceptions = exceptions_result.scalars().all()

    # Get findings
    findings_query = select(Finding).where(Finding.case_id == case_id)
    findings_result = await db.execute(findings_query)
    findings = findings_result.scalars().all()

    # Get recommendations
    recommendations_query = select(Recommendation).where(Recommendation.case_id == case_id)
    recommendations_result = await db.execute(recommendations_query)
    recommendations = recommendations_result.scalars().all()

    # Get decisions
    decisions_query = select(HumanDecision).where(HumanDecision.case_id == case_id)
    decisions_result = await db.execute(decisions_query)
    decisions = decisions_result.scalars().all()

    return CaseDetailResponse(
        case=case,
        exceptions=exceptions,
        findings=findings,
        recommendations=recommendations,
        decisions=decisions
    )


@app.get("/api/cases/{case_id}/evidence")
async def case_evidence(case_id: str, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(EvidenceSnapshot).where(EvidenceSnapshot.case_id == case_id)
                             .order_by(EvidenceSnapshot.retrieved_at.desc()))).scalars().all()
    return [{"id": row.id, "evidence_type": row.evidence_type, "status": row.evidence_status,
             "source_system": row.source_system, "retrieved_at": row.retrieved_at,
             "source_record_ids": (row.evidence_data or {}).get("source_record_ids", [])} for row in rows]


@app.get("/api/cases/{case_id}/timeline")
async def case_timeline(case_id: str, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(AuditEvent).where(AuditEvent.case_id == case_id)
                             .order_by(AuditEvent.seq.desc()).limit(200))).scalars().all()
    return [{"id": row.id, "seq": row.seq, "event_type": row.event_type, "actor": row.actor,
             "description": row.action_description, "created_at": row.created_at} for row in rows]


@app.get("/api/cases/{case_id}/collaboration")
async def case_collaboration(case_id: str, db: AsyncSession = Depends(get_db)):
    notes = (await db.execute(select(CaseNote).where(CaseNote.case_id == case_id).order_by(CaseNote.created_at.desc()))).scalars().all()
    requests = (await db.execute(select(EvidenceRequest).where(EvidenceRequest.case_id == case_id).order_by(EvidenceRequest.created_at.desc()))).scalars().all()
    return {"notes": [{"id": n.id, "author": n.author, "body": n.body, "created_at": n.created_at} for n in notes], "evidence_requests": [{"id": r.id, "requirement": r.requirement, "requested_from": r.requested_from, "requested_by": r.requested_by, "status": r.status, "created_at": r.created_at, "response": r.response, "reference": r.reference, "fulfilled_by": r.fulfilled_by, "fulfilled_at": r.fulfilled_at,
        "rejection_reason": r.rejection_reason, "override_reason": r.override_reason, "override_financial_impact": str(r.override_financial_impact) if r.override_financial_impact is not None else None,
        "override_requested_by": r.override_requested_by, "override_requested_at": r.override_requested_at, "override_approved_by": r.override_approved_by, "override_approved_at": r.override_approved_at} for r in requests]}


@app.get("/api/cases/{case_id}/reconciliation")
async def case_reconciliation(case_id: str, db: AsyncSession = Depends(get_db)):
    """Return the immutable source-record lineage used to create an exception case."""
    case = (await db.execute(select(Case).where(Case.id == case_id))).scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    created = (await db.execute(
        select(AuditEvent).where(AuditEvent.case_id == case_id, AuditEvent.event_type == "CASE_CREATED")
        .order_by(AuditEvent.created_at.asc())
    )).scalars().first()
    linked_ids = (created.after_state or {}).get("transaction_ids", []) if created else []
    contextual = not bool(linked_ids)
    query = select(CanonicalTransaction, SourceRecord).outerjoin(
        SourceRecord, CanonicalTransaction.source_record_id == SourceRecord.id
    )
    if linked_ids:
        query = query.where(CanonicalTransaction.id.in_(linked_ids))
    else:
        # Legacy/demo cases cannot claim a causal link; return only relevant context.
        query = query.where(CanonicalTransaction.store_id == case.store_id,
                            CanonicalTransaction.business_date == case.business_date)
    rows = (await db.execute(query.order_by(CanonicalTransaction.event_timestamp))).all()
    records = [{
        "transaction_id": tx.id, "source_system": source.source_system if source else "Unknown",
        "source_record_id": source.source_record_id if source else None,
        "transaction_type": tx.transaction_type, "business_date": tx.business_date,
        "event_timestamp": tx.event_timestamp, "amount": tx.signed_amount, "currency": tx.currency,
        "payment_reference": tx.payment_reference, "settlement_reference": tx.settlement_reference,
        "settlement_date": tx.settlement_date, "reconciliation_status": tx.reconciliation_status,
        "source_lineage": tx.source_lineage,
    } for tx, source in rows]
    return {"case_id": case_id, "linked": not contextual, "message": (
        "Records recorded by the reconciliation run that created this case."
        if not contextual else "No source links were recorded for this legacy case; showing same-store, same-date context only."
    ), "records": records, "sources": sorted({record["source_system"] for record in records})}


@app.post("/api/cases/{case_id}/notes")
async def add_case_note(case_id: str, body: CaseNoteCreate, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance", "it"))):
    note = CaseNote(id=str(uuid.uuid4()), case_id=case_id, author=user["user"] or "ontology.user", body=body.body.strip())
    db.add(note); nimbus.audit(db, "CASE_NOTE_ADDED", case_id, "CaseNote", note.id, "Case note added", actor=note.author); await db.commit()
    return {"id": note.id, "author": note.author, "body": note.body, "created_at": note.created_at}


@app.post("/api/cases/{case_id}/evidence-requests")
async def request_case_evidence(case_id: str, body: EvidenceRequestCreate, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance", "it"))):
    req = EvidenceRequest(id=str(uuid.uuid4()), case_id=case_id, requirement=body.requirement.strip(), requested_from=body.requested_from.strip(), requested_by=user["user"] or "ontology.user")
    db.add(req); nimbus.audit(db, "EVIDENCE_REQUESTED", case_id, "EvidenceRequest", req.id, f"Requested {req.requirement} from {req.requested_from}", actor=req.requested_by); await db.commit()
    return {"id": req.id, "status": req.status}


@app.post("/api/cases/{case_id}/evidence/provide")
async def provide_case_evidence(case_id: str, body: EvidenceProvide, db: AsyncSession = Depends(get_db),
                                user: dict = Depends(require_role("finance", "it"))):
    """A person supplies or confirms evidence that no ingested record can provide. It fulfils the matching
    evidence request (or records one on the spot), is audited, and counts toward completeness when the
    requirement belongs to the case's exception type."""
    case = (await db.execute(select(Case).where(Case.id == case_id))).scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
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


@app.post("/api/cases/{case_id}/evidence-requests/{request_id}/reject")
async def reject_evidence_request(case_id: str, request_id: str, body: EvidenceReject, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance", "it"))):
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

@app.post("/api/cases/{case_id}/evidence-requests/{request_id}/override")
async def override_evidence_request(case_id: str, request_id: str, body: EvidenceOverride, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance"))):
    req = (await db.execute(select(EvidenceRequest).where(EvidenceRequest.id == request_id, EvidenceRequest.case_id == case_id))).scalar_one_or_none()
    case = (await db.execute(select(Case).where(Case.id == case_id))).scalar_one_or_none()
    if not req or not case or req.status not in ("Open", "Rejected"): raise HTTPException(status_code=409, detail="Open or rejected evidence request not found")
    if not body.rationale.strip() or body.financial_impact < 0: raise HTTPException(status_code=422, detail="A rationale and non-negative financial impact are required")
    actor = user["user"] or "ontology.user"; req.override_reason, req.override_financial_impact, req.override_requested_by, req.override_requested_at = body.rationale.strip(), body.financial_impact, actor, utcnow()
    if body.financial_impact > nimbus.AUTONOMY_LIMIT:
        req.status = "Override Pending"; nimbus.audit(db, "EVIDENCE_OVERRIDE_REQUESTED", case_id, "EvidenceRequest", req.id, "Evidence override awaiting second Finance approver", actor=actor); await db.commit(); return {"id":req.id,"status":req.status,"requires_second_approver":True}
    await apply_evidence_override(db, case, req, actor); await db.commit(); return {"id":req.id,"status":req.status,"requires_second_approver":False}

@app.post("/api/cases/{case_id}/evidence-requests/{request_id}/override/approve")
async def approve_evidence_override(case_id: str, request_id: str, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance"))):
    req = (await db.execute(select(EvidenceRequest).where(EvidenceRequest.id == request_id, EvidenceRequest.case_id == case_id))).scalar_one_or_none(); case = (await db.execute(select(Case).where(Case.id == case_id))).scalar_one_or_none()
    actor = user["user"] or "ontology.user"
    if not req or not case or req.status != "Override Pending": raise HTTPException(status_code=409, detail="Pending evidence override not found")
    if req.override_requested_by == actor: raise HTTPException(status_code=403, detail="A different Finance approver is required")
    await apply_evidence_override(db, case, req, actor); await db.commit(); return {"id":req.id,"status":req.status}


@app.post("/api/cases", response_model=CaseSchema)
async def create_case(case: CaseCreate, db: AsyncSession = Depends(get_db),
                      _: dict = Depends(require_role("finance", "it"))):
    """Create a new case."""
    case_id = str(uuid.uuid4())
    case_number = f"ZA-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:4].upper()}"

    db_case = Case(
        id=case_id,
        case_number=case_number,
        sla_due_at=nimbus.sla_due(case.priority),
        **case.model_dump()
    )
    db.add(db_case)
    await db.commit()
    await db.refresh(db_case)

    # Create audit event
    audit_event = AuditEvent(
        id=str(uuid.uuid4()),
        event_type="CASE_CREATED",
        actor="system",
        object_type="Case",
        object_id=case_id,
        case_id=case_id,
        action_description=f"Case created: {case.case_type}",
        after_state=case.model_dump()
    )
    db.add(audit_event)
    await db.commit()

    return db_case


@app.put("/api/cases/{case_id}", response_model=CaseSchema)
async def update_case(case_id: str, case_update: CaseUpdate, db: AsyncSession = Depends(get_db),
                      user: dict = Depends(require_role("finance", "it"))):
    """Update a case."""
    query = select(Case).where(Case.id == case_id)
    result = await db.execute(query)
    db_case = result.scalar_one_or_none()

    if not db_case:
        raise HTTPException(status_code=404, detail="Case not found")

    # Track old state for audit
    old_state = {
        "status": db_case.status,
        "assigned_to": db_case.assigned_to,
        "priority": db_case.priority
    }

    if case_update.status is not None:
        raise HTTPException(status_code=422, detail="Case status is controlled by workflow transitions")
    if case_update.priority is not None and case_update.priority not in nimbus.SLA_HOURS:
        raise HTTPException(status_code=422, detail="Invalid priority")
    if case_update.priority is not None and user["role"] not in ("finance", "admin"):
        raise HTTPException(status_code=403, detail="Changing priority requires finance role")

    # Update only work-management fields; lifecycle status is server controlled.
    for key, value in case_update.model_dump(exclude_unset=True).items():
        setattr(db_case, key, value)

    db.add(db_case)
    await db.commit()
    await db.refresh(db_case)

    # Create audit event
    audit_event = AuditEvent(
        id=str(uuid.uuid4()),
        event_type="CASE_UPDATED",
        actor="system",
        object_type="Case",
        object_id=case_id,
        case_id=case_id,
        action_description=f"Case updated",
        before_state=old_state,
        after_state=case_update.model_dump(exclude_unset=True)
    )
    db.add(audit_event)
    await db.commit()

    return db_case


# ============= EXCEPTIONS ENDPOINTS =============

@app.get("/api/exceptions", response_model=list[ExceptionSchema])
async def list_exceptions(case_id: str | None = None, db: AsyncSession = Depends(get_db)):
    """Get exceptions, optionally filtered by case."""
    if case_id:
        query = select(DBException).where(DBException.case_id == case_id).order_by(DBException.created_at.desc())
    else:
        query = select(DBException).order_by(DBException.created_at.desc()).limit(100)

    result = await db.execute(query)
    return result.scalars().all()


@app.post("/api/exceptions", response_model=ExceptionSchema)
async def create_exception(exception: ExceptionCreate, db: AsyncSession = Depends(get_db),
                           _: dict = Depends(require_role("finance", "it"))):
    """Create a new exception."""
    exception_id = str(uuid.uuid4())

    data = exception.model_dump()
    db_exception = DBException(
        id=exception_id,
        detection_origin="Audit Rule",
        detection_rule_id=f"RULE-{exception.exception_type}",
        close_impact="Yes" if exception.exception_amount else "No",
        **data
    )
    db.add(db_exception)

    # Update case totals
    case_query = select(Case).where(Case.id == exception.case_id)
    case_result = await db.execute(case_query)
    db_case = case_result.scalar_one_or_none()

    if db_case:
        db_case.total_exception_amount += exception.exception_amount
        db_case.total_exposure += exception.estimated_exposure
        db.add(db_case)

    await db.commit()
    await db.refresh(db_exception)

    return db_exception


# ============= FINDINGS ENDPOINTS =============

@app.get("/api/findings", response_model=list[FindingSchema])
async def list_findings(case_id: str | None = None, db: AsyncSession = Depends(get_db)):
    """Get findings, optionally filtered by case."""
    if case_id:
        query = select(Finding).where(Finding.case_id == case_id)
    else:
        query = select(Finding).order_by(Finding.created_at.desc()).limit(100)

    result = await db.execute(query)
    return result.scalars().all()


@app.post("/api/findings", response_model=FindingSchema)
async def create_finding(finding: FindingCreate, db: AsyncSession = Depends(get_db)):
    """Create a new finding."""
    finding_id = str(uuid.uuid4())

    db_finding = Finding(
        id=finding_id,
        **finding.model_dump()
    )
    db.add(db_finding)
    await db.commit()
    await db.refresh(db_finding)

    # Update case investigation status
    case_query = select(Case).where(Case.id == finding.case_id)
    case_result = await db.execute(case_query)
    db_case = case_result.scalar_one_or_none()

    if db_case and db_case.investigation_status == "Pending":
        db_case.investigation_status = "In Progress"
        db.add(db_case)
        await db.commit()

    return db_finding


# ============= RECOMMENDATIONS ENDPOINTS =============

@app.get("/api/recommendations", response_model=list[RecommendationSchema])
async def list_recommendations(case_id: str | None = None, db: AsyncSession = Depends(get_db)):
    """Get recommendations, optionally filtered by case."""
    if case_id:
        query = select(Recommendation).where(Recommendation.case_id == case_id)
    else:
        query = select(Recommendation).order_by(Recommendation.created_at.desc()).limit(100)

    result = await db.execute(query)
    return result.scalars().all()


@app.post("/api/recommendations", response_model=RecommendationSchema)
async def create_recommendation(recommendation: RecommendationCreate, db: AsyncSession = Depends(get_db)):
    """Create a new recommendation."""
    recommendation_id = str(uuid.uuid4())

    db_recommendation = Recommendation(
        id=recommendation_id,
        **recommendation.model_dump()
    )
    db.add(db_recommendation)
    await db.commit()
    await db.refresh(db_recommendation)

    return db_recommendation


# ============= HUMAN DECISIONS ENDPOINTS =============

@app.get("/api/decisions", response_model=list[HumanDecisionSchema])
async def list_decisions(case_id: str | None = None, db: AsyncSession = Depends(get_db)):
    """Get human decisions, optionally filtered by case."""
    if case_id:
        query = select(HumanDecision).where(HumanDecision.case_id == case_id)
    else:
        query = select(HumanDecision).order_by(HumanDecision.created_at.desc()).limit(100)

    result = await db.execute(query)
    return result.scalars().all()


@app.post("/api/decisions", response_model=HumanDecisionSchema)
async def create_decision(decision: HumanDecisionCreate, db: AsyncSession = Depends(get_db),
                          user: dict = Depends(require_role("finance", "it"))):
    """Record a human decision. Approval passes the policy gate, then dispatches a workflow."""
    case = (await db.execute(select(Case).where(Case.id == decision.case_id))).scalar_one_or_none()
    rec = (await db.execute(select(Recommendation).where(Recommendation.id == decision.recommendation_id))).scalar_one_or_none()
    if not case or not rec or rec.case_id != case.id:
        raise HTTPException(status_code=404, detail="Case or recommendation not found")
    if rec.status != "Proposed":
        raise HTTPException(status_code=409, detail=f"Recommendation already {rec.status}")

    if decision.decision not in ("Approved", "Rejected", "Escalated"):
        raise HTTPException(status_code=422, detail="Unsupported decision")
    rationale = (decision.rationale or "").strip()
    if not rationale:
        raise HTTPException(status_code=422, detail="A decision rationale is required")
    if decision.decision == "Approved" and user["role"] not in ("finance", "admin"):
        raise HTTPException(status_code=403, detail="Approving recommendations requires finance role")

    decision.actor = user["user"] or ("finance.operator" if user["role"] == "finance" else "admin.operator")
    decision.authority_check = "Pending"
    decision_data = decision.model_dump(exclude={"rationale"})
    decision_data["override_reason"] = rationale
    db_decision = HumanDecision(id=str(uuid.uuid4()), **decision_data)
    db.add(db_decision)
    nimbus.audit(db, "HUMAN_DECISION", case.id, "Recommendation", rec.id,
                 f"{decision.decision} by {decision.actor}", actor=decision.actor)

    if decision.decision == "Approved":
        approved_rules = await ontology_integration.refresh()
        evaluation = nimbus.evaluate_policy(db, case, rec, approved_rules)
        if evaluation.outcome == "RequestEvidence":
            raise HTTPException(status_code=422, detail="Policy: required evidence incomplete")
        decision.authority_check = evaluation.outcome
        db_decision.authority_check = evaluation.outcome
        rec.status = "Approved"
        nimbus.start_workflow(db, case, rec)
    elif decision.decision == "Escalated":
        rec.status = "Escalated"
        case.status = "Escalated"
    else:
        rec.status = "Rejected"
        case.status = "Open"
        case.investigation_status = "Pending"

    await db.commit()
    await db.refresh(db_decision)
    return db_decision


# ============= INVESTIGATION / WORKFLOW / VALIDATION =============

@app.post("/api/cases/{case_id}/investigate", response_model=RecommendationSchema)
async def investigate_case(case_id: str, db: AsyncSession = Depends(get_db),
                           _: dict = Depends(require_role("finance", "it"))):
    """Run the Investigation agent for a case."""
    case = (await db.execute(select(Case).where(Case.id == case_id))).scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
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


@app.get("/api/workflows")
async def list_workflows(case_id: str | None = None, db: AsyncSession = Depends(get_db)):
    query = select(Workflow).order_by(Workflow.created_at.desc()).limit(100)
    if case_id:
        query = query.where(Workflow.case_id == case_id)
    rows = (await db.execute(query)).scalars().all()
    return [{"id": w.id, "case_id": w.case_id, "workflow_type": w.workflow_type, "state": w.state,
             "current_step": w.current_step, "created_at": w.created_at} for w in rows]


@app.post("/api/workflows/{workflow_id}/execute")
async def execute_workflow(workflow_id: str, db: AsyncSession = Depends(get_db),
                           _: dict = Depends(require_role("finance", "it"))):
    """Resolution agent executes the approved action and opens a validation obligation."""
    wf = (await db.execute(select(Workflow).where(Workflow.id == workflow_id))).scalar_one_or_none()
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")
    if wf.state != "Pending":
        raise HTTPException(status_code=409, detail=f"Workflow is {wf.state}")
    case = (await db.execute(select(Case).where(Case.id == wf.case_id))).scalar_one()
    ob = nimbus.resolve(db, case, wf)
    await db.commit()
    return {"workflow_id": wf.id, "state": wf.state, "validation_id": ob.id}


@app.get("/api/validations")
async def list_validations(case_id: str | None = None, db: AsyncSession = Depends(get_db)):
    query = select(ValidationObligation).order_by(ValidationObligation.created_at.desc()).limit(100)
    if case_id:
        query = query.where(ValidationObligation.case_id == case_id)
    rows = (await db.execute(query)).scalars().all()
    return [{"id": v.id, "case_id": v.case_id, "workflow_id": v.workflow_id, "status": v.status,
             "expected_observation": v.expected_observation, "verification_result": v.verification_result,
             "due_window_hours": v.due_window_hours} for v in rows]


@app.post("/api/validations/{validation_id}/run")
async def run_validation(validation_id: str, db: AsyncSession = Depends(get_db),
                         req: ValidationRunRequest | None = None,
                         _: dict = Depends(require_role("finance", "it"))):
    """Verify a workflow only from source observations received after it was executed."""
    ob = (await db.execute(select(ValidationObligation).where(ValidationObligation.id == validation_id))).scalar_one_or_none()
    if not ob:
        raise HTTPException(status_code=404, detail="Validation not found")
    if ob.status == "Complete":
        raise HTTPException(status_code=409, detail="Already verified")
    wf = (await db.execute(select(Workflow).where(Workflow.id == ob.workflow_id))).scalar_one()
    case = (await db.execute(select(Case).where(Case.id == ob.case_id))).scalar_one()
    rows = await investigation_service.case_source_rows(db, case)
    requested_source_records = req.source_record_ids if req else []
    fresh = [
        {"source_record_id": record.id, "transaction_id": txn.id, "source_system": record.source_system}
        for txn, record in rows
        if record.received_at >= ob.created_at
        and (not requested_source_records or record.id in requested_source_records)
    ]
    if fresh:
        primary = (await db.execute(select(DBException).where(DBException.case_id == case.id))).scalars().first()
        db.add(EvidenceSnapshot(
            id=str(uuid.uuid4()), case_id=case.id, exception_id=primary.id if primary else "",
            evidence_type="Validation observation", evidence_status="Complete",
            source_system=fresh[0]["source_system"],
            evidence_data={"source_record_ids": [r["source_record_id"] for r in fresh],
                           "validation_id": ob.id}, retrieved_at=utcnow()))
    nimbus.validate(db, case, wf, ob, fresh)
    await db.commit()
    return {"status": ob.status, "verification_result": ob.verification_result, "case_status": case.status}


@app.get("/api/policy-evaluations")
async def list_policy_evaluations(case_id: str | None = None, db: AsyncSession = Depends(get_db)):
    query = select(PolicyEvaluation).order_by(PolicyEvaluation.created_at.desc()).limit(100)
    if case_id:
        query = query.where(PolicyEvaluation.case_id == case_id)
    rows = (await db.execute(query)).scalars().all()
    return [{"id": p.id, "case_id": p.case_id, "rule_version": p.rule_version, "outcome": p.outcome,
             "reasons": p.reasons, "created_at": p.created_at} for p in rows]


# ============= CONNECTORS =============

DEFAULT_CONNECTORS = ["POS Feed", "Payment Processor", "Bank Feed", "ERP / GL"]


@app.get("/api/connectors")
async def list_connectors(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Connector).order_by(Connector.name))).scalars().all()
    if not rows:
        for name in DEFAULT_CONNECTORS:
            db.add(Connector(id=str(uuid.uuid4()), name=name))
        await db.commit()
        rows = (await db.execute(select(Connector).order_by(Connector.name))).scalars().all()
    return [{"id": c.id, "name": c.name, "state": c.state, "mode": c.mode, "last_error": c.last_error,
             "last_checked_at": c.last_checked_at} for c in rows]


@app.post("/api/connectors/{connector_id}/{action}")
async def connector_action(connector_id: str, action: str, db: AsyncSession = Depends(get_db),
                           user: dict = Depends(require_role("it"))):
    """Retry, pause, or resume a connector (audited IT intervention)."""
    if action not in ("retry", "pause", "resume"):
        raise HTTPException(status_code=400, detail="Unknown action")
    c = (await db.execute(select(Connector).where(Connector.id == connector_id))).scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Connector not found")
    if action == "pause":
        c.state, c.mode = "Paused", "Pause Automation"
    else:
        c.state, c.mode, c.last_error = "Healthy", "Governed Automation", None
    c.last_checked_at = datetime.now(timezone.utc)
    nimbus.audit(db, f"CONNECTOR_{action.upper()}", None, "Connector", c.id, f"{action} on {c.name}", actor=user["user"] or "it.operator")
    await db.commit()
    return {"id": c.id, "state": c.state, "mode": c.mode}


# ============= AUDIT TRAIL =============

@app.get("/api/audit", response_model=list[AuditEventSchema])
async def list_audit_events(case_id: str | None = None, limit: int = 100, db: AsyncSession = Depends(get_db)):
    """Append-only audit trail, newest first."""
    query = select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(limit)
    if case_id:
        query = query.where(AuditEvent.case_id == case_id)
    result = await db.execute(query)
    return result.scalars().all()


# ============= DEMO SEED =============

@app.post("/api/seed")
async def seed_demo_data(db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("it"))):
    """Load synthetic demo cases (only if no cases exist)."""
    if (await db.execute(select(func.count(Case.id)))).scalar():
        raise HTTPException(status_code=409, detail="Data already present")

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    demo = [
        ("Timing Difference", "STORE-014", "Ready for Decision", "In Review", "High",
         "TIMING_DIFFERENCE", "Settlement", 550, 0, "Medium",
         "Refund of $550 occurred after 23:00 cutoff; confirmed by processor settlement batch.",
         "Timing", "Disposition Only"),
        ("Duplicate Sales", "STORE-022", "In Progress", "In Investigation", "Normal",
         "DUPLICATE_SALES", "Transaction Audit", 1280, 1280, "High", None, None, None),
        ("Missing Refund", "STORE-007", "Pending", "Open", "Critical",
         "MISSING_REFUND", "Payment Reconciliation", 3420, 3420, "Critical", None, None, None),
        ("Connector Failure", "STORE-031", "Needs Intervention", "Open", "High",
         "CONNECTOR_TIMEOUT", "Ingestion", 0, 0, "Medium", None, None, None),
    ]
    for i, d in enumerate(demo):
        (ctype, store, inv, status, prio, etype, fam, amt, exp, sev, concl, disp, aclass) = d
        cid = str(uuid.uuid4())
        db.add(Case(id=cid, case_number=f"ZA-{today.replace('-', '')}-{i+1:04d}", status=status,
                    case_type=ctype, business_date=today, store_id=store,
                    total_exception_amount=amt, total_exposure=exp, investigation_status=inv,
                    evidence_completeness=100 if concl else 40, priority=prio))
        await db.flush()
        db.add(DBException(id=str(uuid.uuid4()), case_id=cid, exception_type=etype, exception_family=fam,
                           source_system="POS", detection_origin="Audit Rule", detection_rule_id=f"RULE-{etype}",
                           exception_amount=amt, estimated_exposure=exp, severity=sev, confidence="High",
                           close_impact="Yes" if amt else "No"))
        if concl:
            inv_id = f"INV-{i+1:04d}"
            db.add(Finding(id=str(uuid.uuid4()), case_id=cid, investigation_id=inv_id, conclusion=concl,
                           finding_type="Supported Finding", confidence="High",
                           supporting_rationale="Evidence complete and consistent across sources."))
            db.add(Recommendation(id=str(uuid.uuid4()), case_id=cid, investigation_id=inv_id,
                                  disposition_type=disp, action_class=aclass,
                                  expected_workflow="Apply classification -> Schedule validation -> Monitor -> Close",
                                  financial_impact=0, confidence="High"))
        db.add(AuditEvent(id=str(uuid.uuid4()), event_type="CASE_CREATED", actor="system", object_type="Case",
                          object_id=cid, case_id=cid, action_description=f"Case created: {ctype}"))
    await db.commit()
    return {"seeded": len(demo)}
