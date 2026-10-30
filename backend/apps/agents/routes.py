"""Shadow-mode report: how often the Investigation agent's verdict agrees with the human decision."""
from collections import defaultdict

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_role
from database import get_db
import agent_settings
import agent_tools
import investigator
import llm
import nimbus
from models import AuditEvent, Case, Exception as DBException, HumanDecision, Recommendation

router = APIRouter(prefix="/api/agent")


def agent_supported(result: dict | None) -> bool:
    return any(h.get("verdict") == "supported" and h.get("cited_ids") for h in (result or {}).get("hypotheses", []))


def shadow_report(runs: dict, decisions: dict, types: dict) -> dict:
    """runs: case_id -> agent result|None (latest run); decisions: case_id -> latest human decision;
    types: case_id -> exception_type. Agreement = agent supported a cause and the human approved, or the agent
    did not and the human rejected/escalated. 'missed' = agent inconclusive but human approved (too cautious);
    'overconfident' = agent supported but human rejected (the dangerous cell)."""
    out = defaultdict(lambda: {"cases": 0, "agree": 0, "missed": 0, "overconfident": 0, "agent_failed": 0})
    for case_id, result in runs.items():
        d = decisions.get(case_id)
        if d is None:
            continue
        row = out[types.get(case_id, "UNKNOWN")]
        row["cases"] += 1
        if result is None:
            row["agent_failed"] += 1
            continue
        sup, approved = agent_supported(result), d == "Approved"
        if sup == approved:
            row["agree"] += 1
        elif approved:
            row["missed"] += 1
        else:
            row["overconfident"] += 1
    total = {k: sum(r[k] for r in out.values()) for k in ("cases", "agree", "missed", "overconfident", "agent_failed")}
    return {"total": total, "by_exception_type": dict(out)}


@router.get("/shadow")
async def shadow(db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("finance", "it", "admin"))):
    events = (await db.execute(select(AuditEvent).where(AuditEvent.event_type == "AGENT_RUN")
                               .order_by(AuditEvent.created_at))).scalars().all()
    runs = {e.case_id: (e.after_state or {}).get("result") for e in events if e.case_id}  # latest wins
    decs = (await db.execute(select(HumanDecision).order_by(HumanDecision.created_at))).scalars().all()
    decisions = {d.case_id: d.decision for d in decs}
    types = {}
    for x in (await db.execute(select(DBException.case_id, DBException.exception_type))).all():
        types.setdefault(x[0], x[1])
    return shadow_report(runs, decisions, types)


def eligible(case, rec, result) -> bool:
    """Read-only check mirroring the Permit conditions in nimbus.evaluate_policy plus a grounded agent finding.
    It only nominates cases; approval still goes through the finance-role bulk approve and the policy gate."""
    return bool(
        agent_supported(result) and case.status == "In Review" and case.evidence_completeness == 100
        and rec.status == "Proposed" and rec.disposition_type == "Timing" and rec.financial_impact == 0
        and case.total_exception_amount <= nimbus.TIMING_AUTONOMY_LIMIT)


@router.get("/eligible")
async def eligible_cases(db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("finance", "admin"))):
    """Cases the agent proposes for one-click batch approval. Nothing is changed here."""
    events = (await db.execute(select(AuditEvent).where(AuditEvent.event_type == "AGENT_RUN")
                               .order_by(AuditEvent.created_at))).scalars().all()
    runs = {e.case_id: (e.after_state or {}).get("result") for e in events if e.case_id}
    rows = (await db.execute(select(Case, Recommendation).join(Recommendation, Recommendation.case_id == Case.id)
                             .where(Case.status == "In Review", Case.id.in_(list(runs))))).all()
    latest = {}
    for c, r in rows:
        if c.id not in latest or r.created_at > latest[c.id][1].created_at:
            latest[c.id] = (c, r)
    out = [{"case_id": c.id, "case_number": c.case_number, "amount": str(c.total_exception_amount),
            "disposition": r.disposition_type} for c, r in latest.values() if eligible(c, r, runs[c.id])]
    return {"count": len(out), "cases": out,
            "how_to_approve": "POST /api/action-center/bulk with action=approve and these case ids"}


AGENT_ACTORS = ["reconciliation-engine", "investigation-agent", "policy-engine", "orchestrator",
                "resolution-agent", "validation-agent"]


def agent_registry(cfg: dict) -> list[dict]:
    """What each agent is and is allowed to do, derived from live configuration (no secrets)."""
    import os

    import ontology
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
    import os
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
    from ontology_integration import integration
    from ontology_bindings import database_bindings
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
