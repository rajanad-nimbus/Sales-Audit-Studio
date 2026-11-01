"""Case exceptions, findings, recommendations, and human decisions."""

import uuid

from database import get_db
from fastapi import APIRouter, Depends, HTTPException
from integrations.ontology import nimbus
from integrations.ontology.ontology_integration import integration as ontology_integration
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.cases.models import Case, Finding, HumanDecision, Recommendation
from apps.cases.models import Exception as DBException
from apps.cases.schemas import Exception as ExceptionSchema
from apps.cases.schemas import (
    ExceptionCreate,
    FindingCreate,
    HumanDecisionCreate,
    RecommendationCreate,
)
from apps.cases.schemas import Finding as FindingSchema
from apps.cases.schemas import HumanDecision as HumanDecisionSchema
from apps.cases.schemas import Recommendation as RecommendationSchema
from apps.cases.service import locked_scoped_case
from apps.security import scope
from apps.security.authentication import require_role

router = APIRouter()

@router.get("/api/exceptions", response_model=list[ExceptionSchema])
async def list_exceptions(case_id: str | None = None, db: AsyncSession = Depends(get_db),
                          user: dict = Depends(require_role("analyst", "finance", "it", "auditor"))):
    """Get exceptions, optionally filtered by case."""
    if case_id:
        query = select(DBException).join(Case, Case.id == DBException.case_id).where(DBException.case_id == case_id)
    else:
        query = select(DBException).join(Case, Case.id == DBException.case_id)
    query = scope.restrict(query, user, Case.store_id).order_by(DBException.created_at.desc()).limit(100)

    result = await db.execute(query)
    return result.scalars().all()


@router.post("/api/exceptions", response_model=ExceptionSchema)
async def create_exception(exception: ExceptionCreate, db: AsyncSession = Depends(get_db),
                           user: dict = Depends(require_role("finance", "it"))):
    """Create a new exception."""
    db_case = await locked_scoped_case(db, exception.case_id, user)
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

    db_case.total_exception_amount += exception.exception_amount
    db_case.total_exposure += exception.estimated_exposure
    db_case.amount_needs_review = True

    await db.commit()
    await db.refresh(db_exception)

    return db_exception


@router.get("/api/findings", response_model=list[FindingSchema])
async def list_findings(case_id: str | None = None, db: AsyncSession = Depends(get_db),
                        user: dict = Depends(require_role("analyst", "finance", "it", "auditor"))):
    """Get findings, optionally filtered by case."""
    if case_id:
        query = select(Finding).join(Case, Case.id == Finding.case_id).where(Finding.case_id == case_id)
    else:
        query = select(Finding).join(Case, Case.id == Finding.case_id)

    query = scope.restrict(query, user, Case.store_id).order_by(Finding.created_at.desc()).limit(100)
    result = await db.execute(query)
    return result.scalars().all()


@router.post("/api/findings", response_model=FindingSchema)
async def create_finding(finding: FindingCreate, db: AsyncSession = Depends(get_db),
                         user: dict = Depends(require_role("finance", "it"))):
    """Create a new finding."""
    db_case = await locked_scoped_case(db, finding.case_id, user)
    finding_id = str(uuid.uuid4())

    db_finding = Finding(
        id=finding_id,
        **finding.model_dump()
    )
    db.add(db_finding)
    if db_case.investigation_status == "Pending":
        db_case.investigation_status = "In Progress"
    await db.commit()
    await db.refresh(db_finding)
    return db_finding


@router.get("/api/recommendations", response_model=list[RecommendationSchema])
async def list_recommendations(case_id: str | None = None, db: AsyncSession = Depends(get_db),
                               user: dict = Depends(require_role("analyst", "finance", "it", "auditor"))):
    """Get recommendations, optionally filtered by case."""
    if case_id:
        query = select(Recommendation).join(Case, Case.id == Recommendation.case_id).where(Recommendation.case_id == case_id)
    else:
        query = select(Recommendation).join(Case, Case.id == Recommendation.case_id)

    query = scope.restrict(query, user, Case.store_id).order_by(Recommendation.created_at.desc()).limit(100)
    result = await db.execute(query)
    return result.scalars().all()


@router.post("/api/recommendations", response_model=RecommendationSchema)
async def create_recommendation(recommendation: RecommendationCreate, db: AsyncSession = Depends(get_db),
                                user: dict = Depends(require_role("finance", "it"))):
    """Create a new recommendation."""
    case = await locked_scoped_case(db, recommendation.case_id, user)
    proposed = (await db.execute(select(Recommendation).where(
        Recommendation.case_id == case.id, Recommendation.status == "Proposed"
    ).with_for_update())).scalars().all()
    for previous in proposed:
        previous.status = "Superseded"
        nimbus.audit(db, "RECOMMENDATION_SUPERSEDED", case.id, "Recommendation", previous.id,
                     "Superseded by a newer recommendation",
                     actor=user["user"] or "ontology.user")
    if proposed:
        await db.flush()
    recommendation_id = str(uuid.uuid4())

    db_recommendation = Recommendation(
        id=recommendation_id,
        **recommendation.model_dump()
    )
    db.add(db_recommendation)
    nimbus.audit(db, "RECOMMENDATION_CREATED", case.id, "Recommendation", recommendation_id,
                 f"Recommendation proposed: {db_recommendation.disposition_type}",
                 actor=user["user"] or "ontology.user")
    await db.commit()
    await db.refresh(db_recommendation)

    return db_recommendation


@router.get("/api/decisions", response_model=list[HumanDecisionSchema])
async def list_decisions(case_id: str | None = None, db: AsyncSession = Depends(get_db),
                         user: dict = Depends(require_role("analyst", "finance", "it", "auditor"))):
    """Get human decisions, optionally filtered by case."""
    if case_id:
        query = select(HumanDecision).join(Case, Case.id == HumanDecision.case_id).where(HumanDecision.case_id == case_id)
    else:
        query = select(HumanDecision).join(Case, Case.id == HumanDecision.case_id)

    query = scope.restrict(query, user, Case.store_id).order_by(HumanDecision.created_at.desc()).limit(100)
    result = await db.execute(query)
    return result.scalars().all()


@router.post("/api/decisions", response_model=HumanDecisionSchema)
async def create_decision(decision: HumanDecisionCreate, db: AsyncSession = Depends(get_db),
                          user: dict = Depends(require_role("finance", "it"))):
    """Record a human decision. Approval passes the policy gate, then dispatches a workflow."""
    case = (await db.execute(select(Case).where(Case.id == decision.case_id).with_for_update()
                             .execution_options(populate_existing=True))).scalar_one_or_none()
    rec = (await db.execute(select(Recommendation).where(Recommendation.id == decision.recommendation_id)
                            .with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
    if not case or not rec or rec.case_id != case.id:
        raise HTTPException(status_code=404, detail="Case or recommendation not found")
    scope.require_store(user, case.store_id)
    if case.amount_needs_review:
        raise HTTPException(status_code=409, detail="Case amounts require finance review before a decision")
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
