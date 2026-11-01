"""Execute bounded, audited Action Center bulk operations."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from integrations.ontology import nimbus
from integrations.ontology.ontology_integration import integration as ontology_integration
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.agents import investigation_service
from apps.cases.models import (
    Case,
    HumanDecision,
    Recommendation,
    ValidationObligation,
    Workflow,
)
from apps.cases.models import (
    Exception as DBException,
)

BULK_LIMIT = 1000
HEAVY_LIMIT = 200
BULK_APPROVE_MAX_IMPACT = 5000


@dataclass(slots=True)
class ActionData:
    exceptions: dict[str, list[DBException]] = field(default_factory=dict)
    recommendations: dict[str, Recommendation] = field(default_factory=dict)
    pending_workflows: dict[str, Workflow] = field(default_factory=dict)
    incomplete_obligations: dict[str, ValidationObligation] = field(default_factory=dict)
    validation_workflows: dict[str, Workflow] = field(default_factory=dict)
    evidence_cache: investigation_service.InvestigationEvidenceCache | None = None
    approved_rule_codes: frozenset[str] | None = None


def first_record_per_case(model, case_ids: list[str], *conditions):
    """Select each case's earliest matching child record in the database."""
    ranked = select(
        model.id.label("record_id"),
        func.row_number().over(
            partition_by=model.case_id,
            order_by=(model.created_at, model.id),
        ).label("row_number"),
    ).where(model.case_id.in_(case_ids), *conditions).subquery()
    return select(model).join(ranked, model.id == ranked.c.record_id).where(ranked.c.row_number == 1)


async def _load_action_data(db: AsyncSession, action: str, cases: list[Case]) -> ActionData:
    """Fetch per-case dependencies in bounded set queries instead of N+1 lookups."""
    data = ActionData()
    case_ids = [case.id for case in cases]
    if action == "investigate" and case_ids:
        rows = (await db.execute(
            select(DBException).where(DBException.case_id.in_(case_ids))
            .order_by(DBException.case_id, DBException.created_at, DBException.id)
        )).scalars()
        for row in rows:
            data.exceptions.setdefault(row.case_id, []).append(row)

    if action in {"approve", "escalate"} and case_ids:
        rows = (await db.execute(
            select(Recommendation).where(
                Recommendation.case_id.in_(case_ids), Recommendation.status == "Proposed",
            ).order_by(Recommendation.case_id, Recommendation.created_at.desc(), Recommendation.id.desc())
        )).scalars()
        for row in rows:
            data.recommendations.setdefault(row.case_id, row)

    if action == "advance":
        resolving_ids = [case.id for case in cases if case.status == "Resolving"]
        if resolving_ids:
            rows = (await db.execute(
                first_record_per_case(Workflow, resolving_ids, Workflow.state == "Pending")
            )).scalars()
            data.pending_workflows = {row.case_id: row for row in rows}

        validation_ids = [case.id for case in cases if case.status == "Pending Validation"]
        if validation_ids:
            rows = (await db.execute(first_record_per_case(
                ValidationObligation, validation_ids, ValidationObligation.status != "Complete",
            ))).scalars()
            data.incomplete_obligations = {row.case_id: row for row in rows}

        workflow_ids = {row.workflow_id for row in data.incomplete_obligations.values()}
        if workflow_ids:
            rows = (await db.execute(select(Workflow).where(Workflow.id.in_(workflow_ids)))).scalars()
            data.validation_workflows = {row.id: row for row in rows}

    if action == "investigate":
        data.evidence_cache = investigation_service.InvestigationEvidenceCache(db)
    return data


def _skip(case: Case, reason: str, skipped: list[dict[str, str]]) -> None:
    skipped.append({"case_number": case.case_number, "reason": reason})


def _apply_simple_action(
    case: Case, action: str, params: dict, actor: str, now: datetime, skipped: list[dict[str, str]]
) -> bool:
    if action == "assign":
        case.assigned_to = actor if params.get("assignee", "me") == "me" else params["assignee"]
    elif action == "unassign":
        case.assigned_to = None
    elif action == "priority":
        priority = params.get("priority")
        if priority not in nimbus.SLA_HOURS:
            _skip(case, "invalid priority", skipped)
            return False
        case.priority = priority
        case.sla_due_at = nimbus.sla_due(priority)
    elif action == "snooze":
        case.snoozed_until = now + timedelta(hours=float(params.get("hours", 24)))
    elif action == "unsnooze":
        case.snoozed_until = None
    else:
        return False
    return True


async def _investigate_case(db, case, data: ActionData, skipped) -> bool:
    if case.amount_needs_review:
        _skip(case, "case amounts require finance review", skipped)
        return False
    if case.status not in {"Open", "In Investigation"}:
        _skip(case, f"already {case.status}", skipped)
        return False
    exceptions = data.exceptions.get(case.id, [])
    if not exceptions:
        _skip(case, "no exception records to investigate", skipped)
        return False
    await investigation_service.run_investigation(db, case, exceptions, evidence_cache=data.evidence_cache)
    return True


async def _decide_case(db, case, action, user, actor, data, skipped) -> bool:
    if user["role"] not in {"finance", "admin"}:
        _skip(case, "requires finance role", skipped)
        return False
    if case.status != "In Review":
        _skip(case, f"not awaiting decision ({case.status})", skipped)
        return False
    recommendation = data.recommendations.get(case.id)
    if recommendation is None:
        _skip(case, "no open recommendation", skipped)
        return False

    if action == "approve":
        if recommendation.financial_impact != 0 and case.currency != nimbus.THRESHOLD_CURRENCY:
            _skip(case, "bulk financial limit is in USD; review this currency individually", skipped)
            return False
        if abs(recommendation.financial_impact) > BULK_APPROVE_MAX_IMPACT:
            _skip(case, f"financial movement above bulk limit ({BULK_APPROVE_MAX_IMPACT}); review individually", skipped)
            return False
        if data.approved_rule_codes is None:
            data.approved_rule_codes = await ontology_integration.refresh()
        if nimbus.evaluate_policy(db, case, recommendation, data.approved_rule_codes).outcome == "RequestEvidence":
            _skip(case, "policy: evidence incomplete", skipped)
            return False
        recommendation.status = "Approved"
        nimbus.start_workflow(db, case, recommendation)
        decision = "Approved"
    else:
        recommendation.status = "Escalated"
        case.status = "Escalated"
        decision = "Escalated"

    db.add(HumanDecision(
        id=str(uuid.uuid4()), case_id=case.id, recommendation_id=recommendation.id,
        decision_type="Finance", decision=decision, actor=actor, authority_check="Bulk",
    ))
    nimbus.audit(db, "HUMAN_DECISION", case.id, "Recommendation", recommendation.id,
                 f"{decision} by {actor} (bulk)", actor=actor)
    return True


def _advance_case(db, case, data: ActionData, skipped) -> bool:
    if case.status == "Resolving":
        workflow = data.pending_workflows.get(case.id)
        if workflow is None:
            _skip(case, "no pending workflow", skipped)
            return False
        nimbus.resolve(db, case, workflow)
        return True
    if case.status == "Pending Validation":
        obligation = data.incomplete_obligations.get(case.id)
        if obligation is None:
            _skip(case, "nothing to validate", skipped)
            return False
        workflow = data.validation_workflows.get(obligation.workflow_id)
        if workflow is None:
            raise HTTPException(status_code=409, detail="Validation workflow no longer exists")
        nimbus.validate(db, case, workflow, obligation, [])
        _skip(case, "validation waiting: fresh source evidence required, validate individually", skipped)
        return False
    _skip(case, f"nothing to advance ({case.status})", skipped)
    return False


async def _apply_case_action(db, case, action, params, user, actor, data, now, skipped) -> bool:
    if action in {"assign", "unassign", "priority", "snooze", "unsnooze"}:
        return _apply_simple_action(case, action, params, actor, now, skipped)
    if action == "investigate":
        return await _investigate_case(db, case, data, skipped)
    if action in {"approve", "escalate"}:
        return await _decide_case(db, case, action, user, actor, data, skipped)
    if action == "advance":
        return _advance_case(db, case, data, skipped)
    raise HTTPException(status_code=400, detail=f"Unknown action {action}")


async def execute_bulk_action(
    db: AsyncSession,
    action: str,
    params: dict,
    cases: list[Case],
    user: dict,
    actor: str,
    truncated: bool,
) -> dict:
    """Apply one action to a preselected case set, audit it, and commit atomically."""
    data = await _load_action_data(db, action, cases)
    now = datetime.now(timezone.utc)
    skipped: list[dict[str, str]] = []
    succeeded = 0
    for case in cases:
        succeeded += await _apply_case_action(db, case, action, params, user, actor, data, now, skipped)

    nimbus.audit(
        db, "BULK_ACTION", None, "Case", "bulk",
        f"{actor} ran '{action}' on {len(cases)} case(s): {succeeded} ok, {len(skipped)} skipped",
        actor=actor, after={"action": action, "params": params},
    )
    await db.commit()
    return {
        "action": action,
        "requested": len(cases),
        "ok": succeeded,
        "skipped_count": len(skipped),
        "skipped": skipped[:50],
        "truncated": truncated,
        "limits": {
            "bulk": BULK_LIMIT,
            "agent_actions": HEAVY_LIMIT,
            "approve_max_financial_movement": BULK_APPROVE_MAX_IMPACT,
        },
    }
