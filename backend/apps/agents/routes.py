"""Shadow-mode report: how often the Investigation agent's verdict agrees with the human decision."""
from collections import defaultdict

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.security.authentication import require_role
from database import get_db
from apps.agents import agent_settings, investigator, llm
from apps.security import scope
from integrations.ontology import nimbus
from apps.audit.models import AuditEvent
from apps.cases.models import (
    Case,
    Exception as DBException,
    HumanDecision,
    Recommendation,
)

router = APIRouter(prefix="/api/agent")
MAX_ELIGIBLE_CASES = 100


def agent_supported(result: dict | None) -> bool:
    if not isinstance(result, dict):
        return False
    hypotheses = result.get("hypotheses")
    if not isinstance(hypotheses, list):
        return False
    return any(
        isinstance(hypothesis, dict)
        and hypothesis.get("verdict") == "supported"
        and hypothesis.get("cited_ids")
        for hypothesis in hypotheses
    )


def shadow_report(runs: dict, decisions: dict, types: dict) -> dict:
    """runs: case_id -> agent result|None (latest run); decisions: case_id -> latest human decision;
    types: case_id -> exception_type. Agreement = agent supported a cause and the human approved, or the agent
    did not and the human rejected/escalated. 'missed' = agent inconclusive but human approved (too cautious);
    'overconfident' = agent supported but human rejected (the dangerous cell)."""
    out = defaultdict(lambda: {"cases": 0, "agree": 0, "missed": 0, "overconfident": 0, "agent_failed": 0})
    for case_id, result in runs.items():
        add_shadow_case(out, decisions.get(case_id), result, types.get(case_id))
    return shadow_report_totals(out)


def add_shadow_case(out: dict, decision: str | None, result: dict | None, exception_type: str | None) -> None:
    """Add one compared case to a bounded aggregate keyed only by exception type."""
    if decision is None:
        return
    row = out[exception_type or "UNKNOWN"]
    row["cases"] += 1
    if result is None:
        row["agent_failed"] += 1
        return
    supported, approved = agent_supported(result), decision == "Approved"
    if supported == approved:
        row["agree"] += 1
    elif approved:
        row["missed"] += 1
    else:
        row["overconfident"] += 1


def shadow_report_totals(out: dict) -> dict:
    """Format incremental shadow counts using the stable report response shape."""
    total = {
        key: sum(row[key] for row in out.values())
        for key in ("cases", "agree", "missed", "overconfident", "agent_failed")
    }
    return {"total": total, "by_exception_type": dict(out)}


@router.get("/shadow")
async def shadow(db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("finance", "it", "admin"))):
    visible_cases = scope.restrict(select(Case.id), _, Case.store_id)
    ranked_runs = select(
        AuditEvent.case_id.label("case_id"),
        AuditEvent.after_state.label("after_state"),
        func.row_number().over(
            partition_by=AuditEvent.case_id,
            order_by=(AuditEvent.created_at.desc(), AuditEvent.id.desc()),
        ).label("row_number"),
    ).where(
        AuditEvent.event_type == "AGENT_RUN",
        AuditEvent.case_id.in_(visible_cases),
    ).subquery()
    ranked_decisions = select(
        HumanDecision.case_id.label("case_id"),
        HumanDecision.decision.label("decision"),
        func.row_number().over(
            partition_by=HumanDecision.case_id,
            order_by=(HumanDecision.created_at.desc(), HumanDecision.id.desc()),
        ).label("row_number"),
    ).where(HumanDecision.case_id.in_(visible_cases)).subquery()
    exception_types = (
        select(DBException.case_id.label("case_id"), func.min(DBException.exception_type).label("exception_type"))
        .where(DBException.case_id.in_(visible_cases))
        .group_by(DBException.case_id)
        .subquery()
    )
    query = select(
        ranked_runs.c.after_state,
        ranked_decisions.c.decision,
        exception_types.c.exception_type,
    ).join(
        ranked_decisions,
        ranked_decisions.c.case_id == ranked_runs.c.case_id,
    ).outerjoin(
        exception_types,
        exception_types.c.case_id == ranked_runs.c.case_id,
    ).where(
        ranked_runs.c.row_number == 1,
        ranked_decisions.c.row_number == 1,
    )
    result = await db.stream(query.execution_options(yield_per=500))
    counts = defaultdict(lambda: {
        "cases": 0, "agree": 0, "missed": 0, "overconfident": 0, "agent_failed": 0,
    })
    try:
        async for state, decision, exception_type in result:
            agent_result = state.get("result") if isinstance(state, dict) else None
            add_shadow_case(counts, decision, agent_result, exception_type)
    finally:
        await result.close()
    return shadow_report_totals(counts)


def eligible(case, rec, result) -> bool:
    """Read-only check mirroring the Permit conditions in nimbus.evaluate_policy plus a grounded agent finding.
    It only nominates cases; approval still goes through the finance-role bulk approve and the policy gate."""
    return bool(
        agent_supported(result) and case.status == "In Review" and case.evidence_completeness == 100
        and not getattr(case, "amount_needs_review", False)
        and case.currency == nimbus.THRESHOLD_CURRENCY
        and rec.status == "Proposed" and rec.disposition_type == "Timing" and rec.financial_impact == 0
        and case.total_exception_amount <= nimbus.TIMING_AUTONOMY_LIMIT)


@router.get("/eligible")
async def eligible_cases(db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("finance", "admin"))):
    """Cases the agent proposes for one-click batch approval. Nothing is changed here."""
    review_cases = scope.restrict(select(Case.id).where(Case.status == "In Review"), _, Case.store_id)
    ranked_runs = select(
        AuditEvent.case_id.label("case_id"),
        AuditEvent.after_state.label("after_state"),
        func.row_number().over(
            partition_by=AuditEvent.case_id,
            order_by=(AuditEvent.created_at.desc(), AuditEvent.id.desc()),
        ).label("row_number"),
    ).where(AuditEvent.event_type == "AGENT_RUN", AuditEvent.case_id.in_(review_cases)).subquery()
    ranked_recommendations = select(
        Recommendation.id.label("recommendation_id"),
        Recommendation.case_id.label("case_id"),
        func.row_number().over(
            partition_by=Recommendation.case_id,
            order_by=(Recommendation.created_at.desc(), Recommendation.id.desc()),
        ).label("row_number"),
    ).subquery()
    query = select(Case, Recommendation).join(
        ranked_recommendations,
        (ranked_recommendations.c.case_id == Case.id) & (ranked_recommendations.c.row_number == 1),
    ).join(Recommendation, Recommendation.id == ranked_recommendations.c.recommendation_id).where(
        Case.status == "In Review",
    ).join(
        ranked_runs,
        (ranked_runs.c.case_id == Case.id) & (ranked_runs.c.row_number == 1),
    ).add_columns(ranked_runs.c.after_state).order_by(
        Case.total_exposure.desc(), Case.id,
    )
    result = await db.stream(scope.restrict(query, _, Case.store_id).execution_options(yield_per=500))
    out = []
    eligible_count = 0
    try:
        async for case, recommendation, state in result:
            agent_result = state.get("result") if isinstance(state, dict) else None
            if not eligible(case, recommendation, agent_result):
                continue
            eligible_count += 1
            if len(out) < MAX_ELIGIBLE_CASES:
                out.append({
                    "case_id": case.id,
                    "case_number": case.case_number,
                    "amount": str(case.total_exception_amount),
                    "currency": case.currency,
                    "disposition": recommendation.disposition_type,
                })
    finally:
        await result.close()
    return {"count": eligible_count, "cases": out, "truncated": eligible_count > len(out),
            "how_to_approve": "POST /api/action-center/bulk with action=approve and these case ids"}


AGENT_ACTORS = ["reconciliation-engine", "investigation-agent", "policy-engine", "orchestrator",
                "resolution-agent", "validation-agent"]


def agent_registry(cfg: dict) -> list[dict]:
    """What each agent is and is allowed to do, derived from live configuration (no secrets)."""

    from integrations.ontology import ontology
    llm_on = llm.credentials() is not None
    agent_on = cfg["investigator_enabled"]
    model = cfg["model"]
    tools = cfg["tools"]
    return [
        {"actor": "reconciliation-engine", "name": "Reconciliation Engine", "kind": "Rules", "enabled": True,
         "role": "Matches POS, processor, bank and ERP records and raises cases for breaks and control failures.",
         "can": ["Create cases and exceptions", "Set priority and SLA from amount thresholds"],
         "cannot": ["Approve, correct or close anything"], "model": None, "tools": [],
         "limits": {"runs": "Daily batch, or on demand by IT"}},
        {"actor": "investigation-agent", "name": "Investigation Agent",
         "kind": "LLM agent" if (agent_on and llm_on) else "Rules + optional narrative", "enabled": True,
         "role": "Collects scoped evidence for a case and tests each ontology candidate cause against it.",
         "can": ["Read transactions, source records and store-day totals for the case's own store and date",
                 "Rank candidate causes as supported, contradicted or inconclusive, citing record ids"],
         "cannot": ["Change data", "Set confidence, disposition or authority", "Cite records it was not shown",
                    "Approve or execute anything"],
         "model": model if llm_on else None, "tools": tools if agent_on and llm_on else [],
         "mode_note": ("LLM agent active" if agent_on and llm_on else
                       "LLM agent off. Evidence completeness is checked by rules" +
                       ("; a short LLM note is added." if llm_on else ".")),
         "limits": {"max_turns": investigator.MAX_TURNS, "max_output_tokens": investigator.MAX_OUTPUT_TOKENS,
                    "timeout_seconds": investigator.TIMEOUT_S, "auto_investigate_after_batch":
                    cfg["auto_investigate"]}},
        {"actor": "policy-engine", "name": "Policy Engine", "kind": "Rules", "enabled": True,
         "role": "Decides whether a recommendation needs evidence, a human, or could be permitted.",
         "can": ["Return RequestEvidence, RequireHuman or Permit"], "cannot": ["Act on its own result"],
         "model": None, "tools": [],
         "limits": {"policy_version": nimbus.POLICY_VERSION, "ontology_version": ontology.ONTOLOGY_VERSION,
                    "autonomy_limit": str(nimbus.AUTONOMY_LIMIT), "timing_autonomy_limit": str(nimbus.TIMING_AUTONOMY_LIMIT)}},
        {"actor": "orchestrator", "name": "Orchestrator", "kind": "Rules", "enabled": True,
         "role": "Starts the workflow for an approved recommendation.", "can": ["Create workflows"],
         "cannot": ["Approve recommendations"], "model": None, "tools": [], "limits": {}},
        {"actor": "resolution-agent", "name": "Resolution Agent", "kind": "Rules", "enabled": True,
         "role": "Marks an approved workflow executed and opens a validation obligation.",
         "can": ["Advance approved workflows"], "cannot": ["Run without a human approval", "Call external systems (not built yet)"],
         "model": None, "tools": [], "limits": {}},
        {"actor": "validation-agent", "name": "Validation Agent", "kind": "Rules", "enabled": True,
         "role": "Closes a case when fresh linked source evidence arrives after the action.",
         "can": ["Close validated cases"], "cannot": ["Close without fresh evidence"], "model": None, "tools": [], "limits": {}},
    ]


@router.get("/config")
async def agent_config(request: Request, db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("finance", "it"))):
    """Agents as configured, with live activity from the audit trail."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import func
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    rows = (await db.execute(select(AuditEvent.actor, func.count(), func.max(AuditEvent.created_at))
                             .where(AuditEvent.actor.in_(AGENT_ACTORS)).group_by(AuditEvent.actor))).all()
    recent = dict((await db.execute(select(AuditEvent.actor, func.count())
                                    .where(AuditEvent.actor.in_(AGENT_ACTORS), AuditEvent.created_at >= since)
                                    .group_by(AuditEvent.actor))).all())
    total = {a: (n, last) for a, n, last in rows}
    runs = (await db.execute(select(AuditEvent).where(AuditEvent.event_type == "AGENT_RUN")
                             .order_by(AuditEvent.created_at.desc()).limit(200))).scalars().all()
    failed = sum(1 for r in runs if not (r.after_state or {}).get("result"))
    agents = []
    cfg = await agent_settings.load(db)
    for a in agent_registry(cfg):
        n, last = total.get(a["actor"], (0, None))
        agents.append({**a, "events_total": n, "events_24h": recent.get(a["actor"], 0), "last_active": last})
    history = (await db.execute(select(AuditEvent).where(AuditEvent.event_type == "AGENT_SETTING_CHANGED")
                                .order_by(AuditEvent.created_at.desc()).limit(10))).scalars().all()
    return {"agents": agents,
            "settings": cfg, "llm_key_configured": llm.credentials() is not None,
            "llm_auth": llm.credentials(),
            "options": {"models": agent_settings.ALLOWED_MODELS, "tools": agent_settings.TOOL_NAMES},
            "settings_history": [{"at": h.created_at, "by": h.actor, **(h.after_state or {})} for h in history],
            "human_in_loop": "Every approval, correction and store-day close needs a person with the right role. "
                             "No agent acts without one.",
            "agent_runs": {"recent": len(runs), "failed": failed,
                           "input_tokens": sum((r.after_state or {}).get("input_tokens", 0) for r in runs),
                           "output_tokens": sum((r.after_state or {}).get("output_tokens", 0) for r in runs)}}


@router.get("/ontology")
async def agent_ontology(db: AsyncSession = Depends(get_db),
                         _: dict = Depends(require_role("finance", "it", "admin"))):
    """Compare active enterprise definitions with actual agent behavior and persistence."""
    from integrations.ontology.ontology_integration import integration
    from integrations.ontology.ontology_bindings import database_bindings
    context = integration.context()
    return {"integration": integration.status(),
            "agents": agent_registry(await agent_settings.load(db)),
            "database_bindings": database_bindings(context),
            "workflows": context["definitions"].get("workflows", []),
            "limitations": [
                "Ontology Studio REST currently omits agent-policy rules and full process definitions.",
                "Agent permissions and financial gates remain enforced by Nimbus code.",
                "Missing definitions can also indicate persona scope restrictions.",
                "Database bindings describe schema, not access to financial records.",
            ]}


@router.put("/settings")
async def change_settings(changes: dict, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("admin"))):
    """Admin only. Editable: investigator on/off, auto-investigate, model (allow-list), enabled tools.
    Autonomy limits, policy thresholds and roles are not editable here. Each change is written to the audit trail."""
    if user.get("impersonating"):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Stop impersonating to change agent settings")
    return await agent_settings.update(db, changes, user["user"] or "admin")
