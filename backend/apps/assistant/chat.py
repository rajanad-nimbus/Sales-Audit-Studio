"""Grounded assistant: answers come from live data (cases, evidence, policy, ontology, connectors, batch, audit). Read-only."""
import re
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, or_, select

from apps.security import scope
from integrations.ontology import ontology
from models import (AuditEvent, BatchRun, Case, Exception as DBException, Finding,
                    Recommendation, ValidationObligation, Workflow)
from apps.cases.models import PolicyEvaluation
from apps.system.models import Connector

CASE_REF = re.compile(r"\b(Z[AS]-[A-Z0-9]{4,8}-?[A-Z0-9]{0,8}(?:-[A-Z0-9]+)?)\b", re.I)
VAGUE = re.compile(r"\b(this|that|it|the case|here)\b", re.I)
CASE_CONTEXT_RECORD_LIMIT = 20


def money(v, currency=None) -> str:
    return f"{currency or 'currency unknown'} {Decimal(str(v)):,.2f}"


async def case_context(db, c: Case) -> dict:
    async def recent_records(model):
        rows = (await db.execute(
            select(model).where(model.case_id == c.id)
            .order_by(model.created_at.desc(), model.id.desc())
            .limit(CASE_CONTEXT_RECORD_LIMIT + 1)
        )).scalars().all()
        return rows[:CASE_CONTEXT_RECORD_LIMIT], len(rows) > CASE_CONTEXT_RECORD_LIMIT

    exc, exceptions_truncated = await recent_records(DBException)
    fnd, findings_truncated = await recent_records(Finding)
    rec, recommendations_truncated = await recent_records(Recommendation)
    wf, workflows_truncated = await recent_records(Workflow)
    val, validations_truncated = await recent_records(ValidationObligation)
    pol = (await db.execute(select(PolicyEvaluation).where(PolicyEvaluation.case_id == c.id)
                            .order_by(PolicyEvaluation.created_at.desc()).limit(1))).scalars().all()
    od = ontology.get(exc[0].exception_type) if exc else None
    return {
        "id": c.id, "case_number": c.case_number, "type": c.case_type, "store": c.store_id, "status": c.status,
        "priority": c.priority, "business_date": c.business_date, "assigned_to": c.assigned_to,
        "currency": c.currency,
        "amount_needs_review": c.amount_needs_review,
        "sla_due_at": str(c.sla_due_at) if c.sla_due_at else None,
        "exception_amount": str(c.total_exception_amount), "loss_exposure": str(c.total_exposure),
        "evidence_completeness": c.evidence_completeness,
        "exceptions": [{"type": e.exception_type, "family": e.exception_family, "close_impact": e.close_impact} for e in exc],
        "findings": [{"conclusion": f.conclusion, "confidence": f.confidence} for f in fnd],
        "recommendations": [{"disposition": r.disposition_type, "action_class": r.action_class, "workflow": r.expected_workflow,
                             "financial_impact": str(r.financial_impact), "status": r.status} for r in rec],
        "workflows": [{"state": w.state, "step": w.current_step} for w in wf],
        "validations": [{"status": v.status, "result": v.verification_result} for v in val],
        "truncated": {
            "exceptions": exceptions_truncated,
            "findings": findings_truncated,
            "recommendations": recommendations_truncated,
            "workflows": workflows_truncated,
            "validations": validations_truncated,
        },
        "policy": [{"outcome": p.outcome, "reasons": (p.reasons or {}).get("reasons", []), "version": p.rule_version} for p in pol],
        "ontology": {"required_evidence": od["evidence"], "known_causes": od["known_causes"]} if od else None,
    }


async def build_context(db, case_id: str | None, message: str = "", user: dict | None = None) -> dict:
    user = user or {}
    now = datetime.now(timezone.utc)
    decision_visible = or_(Case.snoozed_until.is_(None), Case.snoozed_until <= now)
    by_status = dict((await db.execute(scope.restrict(select(Case.status, func.count()).group_by(Case.status), user, Case.store_id))).all())
    amounts = (await db.execute(scope.restrict(select(Case.currency, func.sum(Case.total_exception_amount),
        func.sum(Case.total_exposure)).where(Case.status != "Closed", Case.amount_needs_review.is_(False)).group_by(Case.currency), user, Case.store_id))).all()
    stale_count = (await db.execute(scope.restrict(select(func.count()).select_from(Case).where(
        Case.status != "Closed", Case.amount_needs_review.is_(True)), user, Case.store_id))).scalar() or 0
    decision_counts = (await db.execute(scope.restrict(
        select(Case.currency, func.count())
        .where(Case.status == "In Review", decision_visible)
        .group_by(Case.currency),
        user,
        Case.store_id,
    ))).all()
    needs = (await db.execute(scope.restrict(
        select(Case).where(Case.status == "In Review", decision_visible),
        user,
        Case.store_id,
    ).order_by(Case.total_exposure.desc(), Case.id).limit(8))).scalars().all()
    batch = (await db.execute(select(BatchRun).where(BatchRun.status == "Succeeded").order_by(BatchRun.finished_at.desc()).limit(1))).scalars().first()
    ctx = {
        "cases_by_status": by_status,
        "stale_amount_cases": stale_count,
        "needs_decision_count": sum(count for _, count in decision_counts),
        "needs_decision_currencies": [currency for currency, _ in decision_counts],
        "open_amounts_by_currency": [{"currency": currency, "amount": str(amount), "exposure": str(exposure)}
                                     for currency, amount, exposure in amounts],
        "needs_decision": [{"id": c.id, "case_number": c.case_number, "type": c.case_type, "store": c.store_id,
                            "amount": str(c.total_exception_amount), "exposure": str(c.total_exposure), "currency": c.currency} for c in needs],
        "connectors": [{"name": k.name, "state": k.state, "mode": k.mode} for k in (await db.execute(select(Connector))).scalars().all()],
        "last_batch": {"business_date": batch.business_date, "finished_at": str(batch.finished_at),
                       "cases_created": batch.cases_created} if batch else None,
        "recent_activity": [{"actor": a.actor, "what": a.action_description, "case_id": a.case_id} for a in
                            (await db.execute(scope.restrict(select(AuditEvent).join(Case, Case.id == AuditEvent.case_id), user, Case.store_id)
                             .order_by(AuditEvent.created_at.desc()).limit(6) if scope.scoped(user) else
                             select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(6))).scalars().all()],
    }
    references = list(dict.fromkeys(match.upper() for match in CASE_REF.findall(message or "")))
    mentioned = []
    if references:
        mentioned_rows = (await db.execute(scope.restrict(
            select(Case).where(func.upper(Case.case_number).in_(references)),
            user,
            Case.store_id,
        ))).scalars().all()
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
        c = (await db.execute(scope.restrict(select(Case).where(Case.id == case_id), user, Case.store_id))).scalar_one_or_none()
        sel = await case_context(db, c) if c else None
    if sel:
        ctx["selected_case"] = sel
    return ctx


def _src(c: dict) -> dict:
    return {"type": "case", "id": c["id"], "label": c.get("case_number") or c.get("type"), "href": f"/cases/{c['id']}"}


def _needs_src(c: dict) -> dict:
    return {"type": "case", "id": c["id"], "label": f"{c['case_number']} · {c['type']} · {money(c['amount'], c.get('currency'))}", "href": f"/cases/{c['id']}"}


def _ont_match(m: str):
    for key, od in ontology.EXCEPTION_TYPES.items():
        if key.lower().replace("_", " ") in m or od["label"].lower() in m:
            return key, od
    return None, None


def reply(direct: str, bullets: list[str] | None = None, nxt: str | None = None, sources=None, followups=None) -> dict:
    parts = [direct]
    if bullets:
        parts.append("\n".join(f"- {b}" for b in bullets))
    if nxt:
        parts.append(f"**Next:** {nxt}")
    return {"answer": "\n\n".join(parts), "source": "rules", "sources": sources or [], "followups": followups or []}


CASE_FOLLOWUPS = ["What should I do next?", "What is the exposure?", "How complete is the evidence?"]
GLOBAL_FOLLOWUPS = ["What needs my decision?", "Give me a summary", "Which connectors are unhealthy?"]


def rule_answer(msg: str, ctx: dict, persona: str = "") -> dict:
    m = msg.lower().strip()
    sc = ctx.get("selected_case")

    if re.fullmatch(r"(hi|hello|hey|help|what can you do\??|who are you\??)[!. ]*", m):
        return reply("I'm the **Nimbus Assistant**. I explain what is in your data and point you to the right controls. I can't approve, execute or change anything.",
                     ["**Cases and exposure**: what needs a decision, the biggest exposure, SLA status",
                      "**Evidence and findings**: why a case happened and how complete the evidence is",
                      "**Policy and decisions**: what the policy gate decided and what approving would do",
                      "**Definitions**: evidence needed and known causes for each exception type",
                      "**Operations**: connector health, the daily batch, recent activity"],
                     "Select a case, or mention one by number (for example ZA-20261005-0004).", followups=GLOBAL_FOLLOWUPS)

    if not sc and VAGUE.search(m) and re.search(r"\b(why|exposure|evidence|next|status|approve|should|finding)\b", m):
        nd = ctx["needs_decision"][:4]
        return reply("**Which case do you mean?** I don't have one selected, and your question refers to a specific case.",
                     [f"{c['case_number']}: {c['type']} at {c['store']}, {money(c['amount'], c.get('currency'))}" for c in nd] or ["No cases are awaiting a decision right now."],
                     "Select a case in the Command Center or Action Center, or mention its number.",
                     sources=[_needs_src(c) for c in nd], followups=GLOBAL_FOLLOWUPS)

    if sc:
        r = sc["recommendations"][0] if sc["recommendations"] else None
        src = [_src(sc)] + ([{"type": "ontology", "label": ontology.ONTOLOGY_VERSION, "href": None}] if sc.get("ontology") else [])
        head = f"{sc['case_number']} ({sc['type']}, {sc['store']})"
        if re.search(r"why|cause|root|finding|happen|explain", m):
            f = sc["findings"]
            od = sc.get("ontology")
            return reply(f"**{f[0]['conclusion']}**" if f else f"**No finding yet for {head}.** The investigation hasn't run.",
                         ([f"Confidence: {f[0]['confidence']}"] if f else []) + [f"Evidence completeness: {sc['evidence_completeness']}%"]
                         + ([f"Known causes for this type: {', '.join(od['known_causes'])}", "These are candidate causes from the ontology, not proven for this case."] if od else []),
                         "Run the investigation." if not f else ("Provide the missing evidence, then re-run the investigation." if sc["evidence_completeness"] < 100 or f[0]["confidence"] == "Low" else "Review the recommendation and decide."), src, CASE_FOLLOWUPS)
        if re.search(r"exposure|loss|amount|money|impact|how much|cost", m):
            ci = sc["exceptions"][0]["close_impact"] if sc["exceptions"] else "unknown"
            if sc.get("amount_needs_review"):
                return reply(f"**{head}: recorded amounts require finance review after a correction.**",
                             ["The previous amount and exposure are historical and are not current figures."],
                             "Review and record the updated amounts before investigation or export.", src, CASE_FOLLOWUPS)
            return reply(f"**{head}: exception amount {money(sc['exception_amount'], sc.get('currency'))}, estimated loss exposure {money(sc['loss_exposure'], sc.get('currency'))}.**",
                         ["Amount and exposure are tracked separately; exposure can be lower (for example a pure timing difference).",
                          f"Close impact: {ci}", f"Priority {sc['priority']}" + (f", SLA due {sc['sla_due_at'][:16]}" if sc["sla_due_at"] else "")],
                         None, src, CASE_FOLLOWUPS)
        if re.search(r"policy|authority|allowed|permit|rule", m):
            p = sc["policy"][0] if sc["policy"] else None
            return reply(f"**{'Policy ' + p['version'] + ' decided: ' + p['outcome'] if p else 'No policy evaluation yet.'}**",
                         p["reasons"] if p else ["The policy gate runs when a recommendation is approved."], None, src, CASE_FOLLOWUPS)
        if re.search(r"evidence|complete|missing", m):
            od = sc.get("ontology")
            return reply(f"**Evidence completeness for {head} is {sc['evidence_completeness']}%.**",
                         ([f"Required by the ontology: {', '.join(od['required_evidence'])}"] if od else []) + ["Completeness is computed from required evidence, not asserted by a model."],
                         "Run the investigation to collect missing evidence." if sc["evidence_completeness"] < 100 else None, src, CASE_FOLLOWUPS)
        if re.search(r"next|status|stage|what now|should|recommend|approve|do", m):
            if r:
                return reply(f"**{head} is '{sc['status']}'. Proposed action: {r['disposition']} via {r['action_class']} ({r['status']}).**",
                             [f"What happens: {r['workflow']}", f"Financial movement: {money(r['financial_impact'], sc.get('currency'))}"],
                             "Use Approve, Escalate or Request more evidence on the case. I can't decide for you.", src, CASE_FOLLOWUPS)
            return reply(f"**{head} is '{sc['status']}' and has no recommendation yet.**", None, "Run the investigation.", src, CASE_FOLLOWUPS)

    key, od = _ont_match(m)
    if key:
        return reply(f"**{od['label']}** (`{key}`) is a {od['family'].lower()} exception.",
                     [f"Required evidence: {', '.join(od['evidence'])}", f"Known causes: {', '.join(od['known_causes'])}",
                      f"Typical disposition: {od['disposition'][0]} via {od['disposition'][1]}",
                      f"Financial exposure: {'none by default' if od['exposure'] == 'zero' else 'equal to the exception amount'}"],
                     None, [{"type": "ontology", "label": ontology.ONTOLOGY_VERSION, "href": None}],
                     ["What evidence does a missing refund need?", "Give me a summary"])

    if re.search(r"decision|waiting|attention|pending|approve|review|need|work on|priorit", m):
        nd = ctx["needs_decision"]
        decision_count = ctx.get("needs_decision_count", len(nd))
        lead = (f"**{decision_count} case(s) are waiting for a decision.**"
                if decision_count else "**Nothing is waiting for your decision.**")
        extra = []
        if persona == "it":
            bad = [k for k in ctx["connectors"] if k["state"] != "Healthy"]
            extra = [f"Connector issues: {', '.join(k['name'] + ' (' + k['state'] + ')' for k in bad)}"] if bad else ["All connectors are healthy."]
        return reply(lead, [f"{c['case_number']}: {c['type']} at {c['store']}, exposure {money(c['exposure'], c.get('currency'))}" for c in nd[:5]] + extra,
                     "Review the cases in the Action Center." if nd else None, [_needs_src(c) for c in nd[:5]], GLOBAL_FOLLOWUPS)
    if re.search(r"biggest|largest|top|worst", m):
        currencies = set(ctx.get(
            "needs_decision_currencies",
            [c.get("currency") for c in ctx["needs_decision"]],
        ))
        if len(currencies) > 1 or None in currencies:
            return reply("**Exposures cannot be ranked across different or unknown currencies.**",
                         [f"{c['case_number']}: {money(c['exposure'], c.get('currency'))}" for c in ctx["needs_decision"][:5]],
                         None, [_needs_src(c) for c in ctx["needs_decision"][:5]], GLOBAL_FOLLOWUPS)
        nd = sorted(ctx["needs_decision"], key=lambda c: -Decimal(c["exposure"]))[:3]
        return reply("**Largest open exposure among cases awaiting a decision:**" if nd else "**No cases are awaiting a decision.**",
                     [f"{c['case_number']}: {money(c['exposure'], c.get('currency'))} ({c['type']}, {c['store']})" for c in nd], None, [_needs_src(c) for c in nd], GLOBAL_FOLLOWUPS)
    if re.search(r"connector|health|feed|integration", m):
        ks = ctx["connectors"]
        bad = [k for k in ks if k["state"] != "Healthy"]
        return reply(f"**{len(ks) - len(bad)} of {len(ks)} connectors are healthy.**" if ks else "**No connectors are registered yet.**",
                     [f"{k['name']}: {k['state']} ({k['mode']})" for k in ks],
                     "Configure the source adapters before enabling automated connector controls." if bad else None,
                     followups=GLOBAL_FOLLOWUPS)
    if re.search(r"batch|nightly|daily|data as of|fresh|stale", m):
        b = ctx.get("last_batch")
        return reply(f"**Last successful batch: business date {b['business_date']}.**" if b else "**The daily batch has not run yet.**",
                     [f"Finished {b['finished_at'][:16]}", f"Cases raised: {b['cases_created']}"] if b else None,
                     "Transactions sync once a day; cases appear when the batch finishes.", followups=GLOBAL_FOLLOWUPS)
    if re.search(r"recent|activity|happened|latest|audit", m):
        ra = ctx["recent_activity"][:5]
        return reply("**Latest activity:**" if ra else "**No activity yet.**", [f"{x['actor']}: {x['what']}" for x in ra],
                     "See the Audit Trail for the full, verified record.", followups=GLOBAL_FOLLOWUPS)
    if re.search(r"summary|overview|today|how many|status|total|portfolio", m):
        s = ctx["cases_by_status"]
        amounts = ctx.get("open_amounts_by_currency", [])
        return reply(f"**{sum(s.values()):,} cases.**",
                     [f"{v:,} {k.lower()}" for k, v in s.items()] +
                     ([f"{ctx['stale_amount_cases']} open case(s) have amounts awaiting review and are excluded from totals."] if ctx.get("stale_amount_cases") else []) +
                     [f"{money(a['amount'], a['currency'])} open amount; {money(a['exposure'], a['currency'])} exposure" for a in amounts],
                     None, followups=["What needs my decision?", "Which connectors are unhealthy?"])

    return reply("**I can't answer that from the data I have.**",
                 ["Try: what needs my decision, a summary, the biggest exposure, connector health, the daily batch, or recent activity.",
                  "For a case: why it happened, its exposure, the evidence, the policy decision, or what to do next.",
                  "For definitions: ask what evidence a specific exception type needs."],
                 "Select a case or mention one by number.", followups=GLOBAL_FOLLOWUPS)
