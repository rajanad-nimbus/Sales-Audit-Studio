"""Assemble read-only assistant context from cases, audit, connectors, and operations data."""

import re
from datetime import UTC, datetime

from integrations.ontology import ontology
from integrations.ontology.ontology_integration import integration as ontology_integration
from sqlalchemy import func, or_, select

from apps.assistant.responses import rule_answer as rule_answer
from apps.audit.models import AuditEvent
from apps.cases.models import (
    Case,
    Finding,
    PolicyEvaluation,
    Recommendation,
    ValidationObligation,
    Workflow,
)
from apps.cases.models import (
    Exception as DBException,
)
from apps.operations.models import BatchRun
from apps.security import scope
from apps.system.models import Connector

CASE_REF = re.compile(r"\b(Z[AS]-[A-Z0-9]{4,8}-?[A-Z0-9]{0,8}(?:-[A-Z0-9]+)?)\b", re.I)
CASE_CONTEXT_RECORD_LIMIT = 20


async def case_context(db, c: Case) -> dict:
    async def recent_records(model):
        rows = (
            (
                await db.execute(
                    select(model)
                    .where(model.case_id == c.id)
                    .order_by(model.created_at.desc(), model.id.desc())
                    .limit(CASE_CONTEXT_RECORD_LIMIT + 1)
                )
            )
            .scalars()
            .all()
        )
        return rows[:CASE_CONTEXT_RECORD_LIMIT], len(rows) > CASE_CONTEXT_RECORD_LIMIT

    exc, exceptions_truncated = await recent_records(DBException)
    fnd, findings_truncated = await recent_records(Finding)
    rec, recommendations_truncated = await recent_records(Recommendation)
    wf, workflows_truncated = await recent_records(Workflow)
    val, validations_truncated = await recent_records(ValidationObligation)
    pol = (
        (
            await db.execute(
                select(PolicyEvaluation)
                .where(PolicyEvaluation.case_id == c.id)
                .order_by(PolicyEvaluation.created_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .all()
    )
    od = ontology.get(exc[0].exception_type) if exc else None
    return {
        "id": c.id,
        "case_number": c.case_number,
        "type": c.case_type,
        "store": c.store_id,
        "status": c.status,
        "priority": c.priority,
        "business_date": c.business_date,
        "assigned_to": c.assigned_to,
        "currency": c.currency,
        "amount_needs_review": c.amount_needs_review,
        "sla_due_at": str(c.sla_due_at) if c.sla_due_at else None,
        "exception_amount": str(c.total_exception_amount),
        "loss_exposure": str(c.total_exposure),
        "evidence_completeness": c.evidence_completeness,
        "exceptions": [
            {"type": e.exception_type, "family": e.exception_family, "close_impact": e.close_impact}
            for e in exc
        ],
        "findings": [{"conclusion": f.conclusion, "confidence": f.confidence} for f in fnd],
        "recommendations": [
            {
                "disposition": r.disposition_type,
                "action_class": r.action_class,
                "workflow": r.expected_workflow,
                "financial_impact": str(r.financial_impact),
                "status": r.status,
            }
            for r in rec
        ],
        "workflows": [{"state": w.state, "step": w.current_step} for w in wf],
        "validations": [{"status": v.status, "result": v.verification_result} for v in val],
        "truncated": {
            "exceptions": exceptions_truncated,
            "findings": findings_truncated,
            "recommendations": recommendations_truncated,
            "workflows": workflows_truncated,
            "validations": validations_truncated,
        },
        "policy": [
            {
                "outcome": p.outcome,
                "reasons": (p.reasons or {}).get("reasons", []),
                "version": p.rule_version,
            }
            for p in pol
        ],
        "ontology": {"required_evidence": od["evidence"], "known_causes": od["known_causes"]}
        if od
        else None,
    }


def ontology_reference(message: str) -> dict | None:
    """Release provenance plus approved glossary entries the question mentions; None when Studio is not connected."""
    context = ontology_integration.context()
    if not context["ontology"]:
        return None
    text = (message or "").lower()
    glossary = [
        row
        for row in context["definitions"].get("glossary", [])
        if any(
            isinstance(v, str) and 3 < len(v) < 60 and v.lower() in text
            for k, v in row.items()
            if k in ("term", "name", "title", "acronym")
        )
    ][:5]
    return {"release": context["ontology"].get("releaseId"), "glossary": glossary}


async def build_context(
    db, case_id: str | None, message: str = "", user: dict | None = None
) -> dict:
    user = user or {}
    now = datetime.now(UTC)
    decision_visible = or_(Case.snoozed_until.is_(None), Case.snoozed_until <= now)
    by_status = dict(
        (
            await db.execute(
                scope.restrict(
                    select(Case.status, func.count()).group_by(Case.status), user, Case.store_id
                )
            )
        ).all()
    )
    amounts = (
        await db.execute(
            scope.restrict(
                select(
                    Case.currency,
                    func.sum(Case.total_exception_amount),
                    func.sum(Case.total_exposure),
                )
                .where(Case.status != "Closed", Case.amount_needs_review.is_(False))
                .group_by(Case.currency),
                user,
                Case.store_id,
            )
        )
    ).all()
    stale_count = (
        await db.execute(
            scope.restrict(
                select(func.count())
                .select_from(Case)
                .where(Case.status != "Closed", Case.amount_needs_review.is_(True)),
                user,
                Case.store_id,
            )
        )
    ).scalar() or 0
    decision_counts = (
        await db.execute(
            scope.restrict(
                select(Case.currency, func.count())
                .where(Case.status == "In Review", decision_visible)
                .group_by(Case.currency),
                user,
                Case.store_id,
            )
        )
    ).all()
    needs = (
        (
            await db.execute(
                scope.restrict(
                    select(Case).where(Case.status == "In Review", decision_visible),
                    user,
                    Case.store_id,
                )
                .order_by(Case.total_exposure.desc(), Case.id)
                .limit(8)
            )
        )
        .scalars()
        .all()
    )
    batch = (
        (
            await db.execute(
                select(BatchRun)
                .where(BatchRun.status == "Succeeded")
                .order_by(BatchRun.finished_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    activity_query = select(AuditEvent.actor, AuditEvent.action_description, AuditEvent.case_id)
    if scope.scoped(user):
        activity_query = activity_query.join(Case, Case.id == AuditEvent.case_id)
        activity_query = scope.restrict(activity_query, user, Case.store_id)
    activity_rows = (
        await db.execute(activity_query.order_by(AuditEvent.created_at.desc()).limit(6))
    ).all()
    ctx = {
        "cases_by_status": by_status,
        "stale_amount_cases": stale_count,
        "needs_decision_count": sum(count for _, count in decision_counts),
        "needs_decision_currencies": [currency for currency, _ in decision_counts],
        "open_amounts_by_currency": [
            {"currency": currency, "amount": str(amount), "exposure": str(exposure)}
            for currency, amount, exposure in amounts
        ],
        "needs_decision": [
            {
                "id": c.id,
                "case_number": c.case_number,
                "type": c.case_type,
                "store": c.store_id,
                "amount": str(c.total_exception_amount),
                "exposure": str(c.total_exposure),
                "currency": c.currency,
            }
            for c in needs
        ],
        "connectors": [
            {"name": k.name, "state": k.state, "mode": k.mode}
            for k in (await db.execute(select(Connector))).scalars().all()
        ],
        "last_batch": {
            "business_date": batch.business_date,
            "finished_at": str(batch.finished_at),
            "cases_created": batch.cases_created,
        }
        if batch
        else None,
        "recent_activity": [
            {"actor": actor, "what": description, "case_id": case_id}
            for actor, description, case_id in activity_rows
        ],
    }
    references = list(dict.fromkeys(match.upper() for match in CASE_REF.findall(message or "")))
    mentioned = []
    if references:
        mentioned_rows = (
            (
                await db.execute(
                    scope.restrict(
                        select(Case).where(func.upper(Case.case_number).in_(references)),
                        user,
                        Case.store_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        cases_by_number = {case.case_number.upper(): case for case in mentioned_rows}
        for reference in references:
            case = cases_by_number.get(reference)
            if case:
                mentioned.append(await case_context(db, case))
                if len(mentioned) == 3:
                    break
    if mentioned:
        ctx["mentioned_cases"] = mentioned
    sel = None
    if mentioned:
        sel = mentioned[0]
    elif case_id:
        c = (
            await db.execute(
                scope.restrict(select(Case).where(Case.id == case_id), user, Case.store_id)
            )
        ).scalar_one_or_none()
        sel = await case_context(db, c) if c else None
    if sel:
        ctx["selected_case"] = sel
    if reference := ontology_reference(message):
        ctx["ontology_reference"] = reference
    return ctx
