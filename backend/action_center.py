"""Action Center: server-side triage at scale (filters, facets, aggregates, bulk actions, export)."""
import csv
import io
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import and_, case as sql_case, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

import investigation_service
import nimbus
from ontology_integration import integration as ontology_integration
from auth import require_role
from database import get_db
from models import Case, Exception as DBException, HumanDecision, Recommendation, Workflow, ValidationObligation

router = APIRouter(prefix="/api/action-center")

STAGES = {
    "open": ["In Review", "Open", "In Investigation", "Resolving", "Pending Validation"],
    "decision": ["In Review"],
    "progress": ["Open", "In Investigation", "Resolving", "Pending Validation"],
    "done": ["Closed", "Escalated"],
}
BULK_LIMIT = 1000          # max cases touched per bulk call
HEAVY_LIMIT = 200          # max cases for agent-driven bulk actions
BULK_APPROVE_MAX_IMPACT = 5000  # bulk approval refuses larger financial movements
SORTS = {
    "sla": Case.sla_due_at, "exposure": Case.total_exposure, "amount": Case.total_exception_amount,
    "age": Case.created_at, "store": Case.store_id, "type": Case.case_type, "date": Case.business_date,
    "case_number": Case.case_number, "status": Case.status, "priority": Case.priority, "evidence": Case.evidence_completeness,
}


class Filters:
    def __init__(self, q: str | None = None, stage: str | None = None, status: str | None = None,
                 case_type: str | None = None, store: str | None = None, priority: str | None = None,
                 assignee: str | None = None, min_amount: float | None = None, max_amount: float | None = None,
                 date_from: str | None = None, date_to: str | None = None, sla: str | None = None,
                 snoozed: str | None = None):
        self.__dict__.update(locals())


def me_name(user: dict) -> str:
    return user["user"] or "demo.user"


def apply(stmt, f: Filters, user: dict, skip: str | None = None):
    now = datetime.now(timezone.utc)
    conds = []
    csv_ = lambda v: [x.strip() for x in v.split(",") if x.strip()]
    if f.q:
        like = f"%{f.q.strip()}%"
        conds.append(or_(Case.case_number.ilike(like), Case.store_id.ilike(like), Case.case_type.ilike(like)))
    if f.stage and skip != "stage" and f.stage in STAGES:
        conds.append(Case.status.in_(STAGES[f.stage]))
    if f.status and skip != "status":
        conds.append(Case.status.in_(csv_(f.status)))
    if f.case_type and skip != "case_type":
        conds.append(Case.case_type.in_(csv_(f.case_type)))
    if f.store and skip != "store":
        conds.append(Case.store_id.in_(csv_(f.store)))
    if f.priority and skip != "priority":
        conds.append(Case.priority.in_(csv_(f.priority)))
    if f.assignee:
        if f.assignee == "unassigned":
            conds.append(Case.assigned_to.is_(None))
        else:
            conds.append(Case.assigned_to == (me_name(user) if f.assignee == "me" else f.assignee))
    if f.min_amount is not None:
        conds.append(Case.total_exception_amount >= f.min_amount)
    if f.max_amount is not None:
        conds.append(Case.total_exception_amount <= f.max_amount)
    if f.date_from:
        conds.append(Case.business_date >= f.date_from)
    if f.date_to:
        conds.append(Case.business_date <= f.date_to)
    if f.sla == "breached":
        conds.append(and_(Case.status != "Closed", Case.sla_due_at < now))
    elif f.sla == "at_risk":
        conds.append(and_(Case.status != "Closed", Case.sla_due_at >= now, Case.sla_due_at < now + timedelta(hours=8)))
    if f.snoozed == "only":
        conds.append(Case.snoozed_until > now)
    elif f.snoozed != "all":
        conds.append(or_(Case.snoozed_until.is_(None), Case.snoozed_until <= now))
    return stmt.where(*conds) if conds else stmt


def filters(q: str | None = None, stage: str | None = None, status: str | None = None, case_type: str | None = None,
            store: str | None = None, priority: str | None = None, assignee: str | None = None,
            min_amount: float | None = None, max_amount: float | None = None, date_from: str | None = None,
            date_to: str | None = None, sla: str | None = None, snoozed: str | None = None) -> Filters:
    return Filters(q, stage, status, case_type, store, priority, assignee, min_amount, max_amount,
                   date_from, date_to, sla, snoozed)


def sla_state(c: Case, now: datetime) -> str:
    if c.status == "Closed" or not c.sla_due_at:
        return "none"
    if c.sla_due_at < now:
        return "breached"
    return "at_risk" if c.sla_due_at < now + timedelta(hours=8) else "ok"


def row(c: Case, now: datetime) -> dict:
    return {"id": c.id, "case_number": c.case_number, "case_type": c.case_type, "store_id": c.store_id,
            "business_date": c.business_date, "status": c.status, "priority": c.priority,
            "amount": str(c.total_exception_amount), "exposure": str(c.total_exposure),
            "evidence": c.evidence_completeness, "investigation_status": c.investigation_status, "assigned_to": c.assigned_to,
            "sla_due_at": c.sla_due_at, "sla_state": sla_state(c, now), "snoozed_until": c.snoozed_until,
            "age_hours": int((now - c.created_at).total_seconds() // 3600)}


@router.get("/cases")
async def list_cases(f: Filters = Depends(filters), sort: str = "sla", dir: str = "asc",
                     page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100),
                     db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance", "it"))):
    now = datetime.now(timezone.utc)
    total = (await db.execute(apply(select(func.count()).select_from(Case), f, user))).scalar() or 0
    col = SORTS.get(sort, Case.sla_due_at)
    order = col.desc().nulls_last() if dir == "desc" else col.asc().nulls_last()
    stmt = apply(select(Case), f, user).order_by(order, Case.id).offset((page - 1) * page_size).limit(page_size)
    items = [row(c, now) for c in (await db.execute(stmt)).scalars().all()]
    return {"items": items, "total": total, "page": page, "page_size": page_size,
            "pages": max(1, -(-total // page_size))}


@router.get("/facets")
async def facets(f: Filters = Depends(filters), db: AsyncSession = Depends(get_db),
                 user: dict = Depends(require_role("finance", "it"))):
    async def by(col, key, limit=None):
        stmt = apply(select(col, func.count()).group_by(col), f, user, skip=key).order_by(func.count().desc())
        if limit:
            stmt = stmt.limit(limit)
        return [{"value": v, "count": n} for v, n in (await db.execute(stmt)).all()]
    return {"status": await by(Case.status, "status"), "priority": await by(Case.priority, "priority"),
            "case_type": await by(Case.case_type, "case_type"), "store": await by(Case.store_id, "store", 25)}


@router.get("/summary")
async def summary(db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance", "it"))):
    now = datetime.now(timezone.utc)
    live = Case.status != "Closed"
    visible = or_(Case.snoozed_until.is_(None), Case.snoozed_until <= now)

    async def one(*conds, expr=func.count()):
        return (await db.execute(select(expr).select_from(Case).where(*conds))).scalar() or 0

    closed = await one(Case.status == "Closed")
    manual = (await db.execute(select(func.count(func.distinct(HumanDecision.case_id))).where(
        HumanDecision.decision_type.in_(["Finance", "IT"])))).scalar() or 0
    days = (await db.execute(select(Case.business_date, func.count()).group_by(Case.business_date)
                             .order_by(Case.business_date.desc()).limit(14))).all()
    return {
        "needs_decision": await one(Case.status == "In Review", visible),
        "agent_prepared": await one(Case.investigation_status == "Ready for Decision", visible),
        "agent_waiting_evidence": await one(Case.investigation_status == "Awaiting Evidence", visible),
        "sla_breached": await one(live, Case.sla_due_at < now, visible),
        "at_risk": await one(live, Case.sla_due_at >= now, Case.sla_due_at < now + timedelta(hours=8), visible),
        "unassigned": await one(live, Case.assigned_to.is_(None), visible),
        "mine": await one(live, Case.assigned_to == me_name(user)),
        "snoozed": await one(live, Case.snoozed_until > now),
        "open_total": await one(live),
        "open_exposure": str(await one(live, expr=func.coalesce(func.sum(Case.total_exposure), 0))),
        "open_amount": str(await one(live, expr=func.coalesce(func.sum(Case.total_exception_amount), 0))),
        "stores_affected": await one(live, expr=func.count(func.distinct(Case.store_id))),
        "closed": closed,
        "straight_through_rate": round(100 * max(closed - manual, 0) / closed, 1) if closed else None,
        "by_day": [{"date": d, "count": n} for d, n in reversed(days)],
    }


@router.get("/groups")
async def groups(by: str = "case_type", f: Filters = Depends(filters), db: AsyncSession = Depends(get_db),
                 user: dict = Depends(require_role("finance", "it"))):
    col = {"case_type": Case.case_type, "store": Case.store_id, "day": Case.business_date,
           "priority": Case.priority, "status": Case.status, "assignee": func.coalesce(Case.assigned_to, "Unassigned")}.get(by)
    if col is None:
        raise HTTPException(status_code=400, detail="Unknown grouping")
    now = datetime.now(timezone.utc)
    stmt = apply(select(
        col.label("k"), func.count(), func.coalesce(func.sum(Case.total_exception_amount), 0),
        func.coalesce(func.sum(Case.total_exposure), 0),
        func.count().filter(and_(Case.status != "Closed", Case.sla_due_at < now)), func.min(Case.created_at),
    ).group_by(col), f, user).order_by(func.sum(Case.total_exposure).desc().nulls_last()).limit(100)
    return [{"key": k, "count": n, "amount": str(a), "exposure": str(e), "breached": b,
             "oldest_hours": int((now - o).total_seconds() // 3600)} for k, n, a, e, b, o in (await db.execute(stmt)).all()]


class BulkRequest(BaseModel):
    action: str
    ids: list[str] | None = None
    select_all: dict | None = None
    params: dict = {}


@router.post("/bulk")
async def bulk(req: BulkRequest, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance", "it"))):
    actor = me_name(user)
    if req.ids:
        stmt = select(Case).where(Case.id.in_(req.ids[:BULK_LIMIT]))
        truncated = len(req.ids) > BULK_LIMIT
    elif req.select_all is not None:
        stmt = apply(select(Case), Filters(**{k: v for k, v in req.select_all.items() if k in Filters.__init__.__code__.co_varnames}), user)
        stmt = stmt.order_by(Case.sla_due_at.asc().nulls_last()).limit(BULK_LIMIT + 1)
        truncated = False
    else:
        raise HTTPException(status_code=400, detail="Provide ids or select_all")
    cases = list((await db.execute(stmt)).scalars().all())
    if req.select_all is not None and len(cases) > BULK_LIMIT:
        cases, truncated = cases[:BULK_LIMIT], True

    heavy = req.action in ("investigate", "approve", "escalate", "advance")
    if heavy and len(cases) > HEAVY_LIMIT:
        cases, truncated = cases[:HEAVY_LIMIT], True

    ok, skipped = 0, []
    skip = lambda c, why: skipped.append({"case_number": c.case_number, "reason": why})
    now = datetime.now(timezone.utc)

    for c in cases:
        a = req.action
        if a == "assign":
            c.assigned_to = actor if req.params.get("assignee", "me") == "me" else req.params["assignee"]; ok += 1
        elif a == "unassign":
            c.assigned_to = None; ok += 1
        elif a == "priority":
            p = req.params.get("priority")
            if p not in nimbus.SLA_HOURS: skip(c, "invalid priority"); continue
            c.priority = p; c.sla_due_at = nimbus.sla_due(p); ok += 1
        elif a == "snooze":
            c.snoozed_until = now + timedelta(hours=float(req.params.get("hours", 24))); ok += 1
        elif a == "unsnooze":
            c.snoozed_until = None; ok += 1
        elif a == "investigate":
            if c.status not in ("Open", "In Investigation"): skip(c, f"already {c.status}"); continue
            excs = (await db.execute(select(DBException).where(DBException.case_id == c.id))).scalars().all()
            if not excs: skip(c, "no exception records to investigate"); continue
            await investigation_service.run_investigation(db, c, excs); ok += 1
        elif a in ("approve", "escalate"):
            if user["role"] not in ("finance", "admin"): skip(c, "requires finance role"); continue
            if c.status != "In Review": skip(c, f"not awaiting decision ({c.status})"); continue
            rec = (await db.execute(select(Recommendation).where(
                Recommendation.case_id == c.id, Recommendation.status == "Proposed"))).scalars().first()
            if not rec: skip(c, "no open recommendation"); continue
            if a == "approve":
                if rec.financial_impact > BULK_APPROVE_MAX_IMPACT:
                    skip(c, f"financial movement above bulk limit ({BULK_APPROVE_MAX_IMPACT}); review individually"); continue
                if nimbus.evaluate_policy(db, c, rec, await ontology_integration.refresh()).outcome == "RequestEvidence":
                    skip(c, "policy: evidence incomplete"); continue
                rec.status = "Approved"; nimbus.start_workflow(db, c, rec)
                decision = "Approved"
            else:
                rec.status, c.status, decision = "Escalated", "Escalated", "Escalated"
            db.add(HumanDecision(id=str(uuid.uuid4()), case_id=c.id, recommendation_id=rec.id, decision_type="Finance",
                                 decision=decision, actor=actor, authority_check="Bulk"))
            nimbus.audit(db, "HUMAN_DECISION", c.id, "Recommendation", rec.id, f"{decision} by {actor} (bulk)", actor=actor)
            ok += 1
        elif a == "advance":
            if c.status == "Resolving":
                wf = (await db.execute(select(Workflow).where(Workflow.case_id == c.id, Workflow.state == "Pending"))).scalars().first()
                if wf: nimbus.resolve(db, c, wf); ok += 1
                else: skip(c, "no pending workflow")
            elif c.status == "Pending Validation":
                ob = (await db.execute(select(ValidationObligation).where(
                    ValidationObligation.case_id == c.id, ValidationObligation.status != "Complete"))).scalars().first()
                if ob:
                    wf = (await db.execute(select(Workflow).where(Workflow.id == ob.workflow_id))).scalar_one()
                    # A bulk transition never substitutes for fresh validation evidence.
                    nimbus.validate(db, c, wf, ob, [])
                    skip(c, "validation waiting: fresh source evidence required, validate individually")
                else: skip(c, "nothing to validate")
            else: skip(c, f"nothing to advance ({c.status})")
        else:
            raise HTTPException(status_code=400, detail=f"Unknown action {a}")

    nimbus.audit(db, "BULK_ACTION", None, "Case", "bulk", f"{actor} ran '{req.action}' on {len(cases)} case(s): {ok} ok, {len(skipped)} skipped",
                 actor=actor, after={"action": req.action, "params": req.params})
    await db.commit()
    return {"action": req.action, "requested": len(cases), "ok": ok, "skipped_count": len(skipped),
            "skipped": skipped[:50], "truncated": truncated,
            "limits": {"bulk": BULK_LIMIT, "agent_actions": HEAVY_LIMIT, "approve_max_financial_movement": BULK_APPROVE_MAX_IMPACT}}


@router.get("/export.csv")
async def export_csv(f: Filters = Depends(filters), sort: str = "sla", dir: str = "asc",
                     db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance", "it"))):
    col = SORTS.get(sort, Case.sla_due_at)
    stmt = apply(select(Case), f, user).order_by(col.desc().nulls_last() if dir == "desc" else col.asc().nulls_last()).limit(50000)
    now = datetime.now(timezone.utc)
    buf = io.StringIO()
    w = csv.writer(buf)
    cols = ["case_number", "case_type", "store_id", "business_date", "status", "priority", "amount", "exposure",
            "evidence", "assigned_to", "sla_due_at", "sla_state", "age_hours"]
    w.writerow(cols)
    for c in (await db.execute(stmt)).scalars().all():
        r = row(c, now)
        w.writerow([r[k] for k in cols])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=action-center.csv"})


TYPES = ["Timing Difference", "Duplicate Sales", "Missing Refund", "Amount Mismatch", "Orphan Payment",
         "Unmatched Sale", "Bank Deposit Variance", "GL Posting Variance", "Incomplete Feed", "Balancing Variance"]


@router.post("/scale-demo")
async def scale_demo(cases: int = Query(50000, ge=1000, le=500000), stores: int = Query(300, ge=10, le=1000),
                     db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("it"))):
    """Bulk-generate synthetic cases in SQL to exercise the UI at volume. Remove with DELETE."""
    types_sql = "ARRAY[" + ",".join(f"'{t}'" for t in TYPES) + "]"
    tag = uuid.uuid4().hex[:4].upper()
    await db.execute(text(f"""
        INSERT INTO cases (id, case_number, status, case_type, business_date, store_id, total_exception_amount,
            total_exposure, investigation_status, evidence_completeness, assigned_to, priority, sla_due_at, created_at, updated_at)
        SELECT gen_random_uuid()::text, 'ZS-{tag}-' || lpad(i::text, 7, '0'),
            CASE WHEN random() < 0.35 THEN 'In Review' WHEN random() < 0.5 THEN 'Open' WHEN random() < 0.4 THEN 'In Investigation'
                 WHEN random() < 0.25 THEN 'Resolving' WHEN random() < 0.2 THEN 'Pending Validation' ELSE 'Closed' END,
            ({types_sql})[1 + floor(random() * {len(TYPES)})::int],
            to_char(current_date - floor(random() * 14)::int, 'YYYY-MM-DD'),
            'STORE-' || lpad((1 + floor(random() * :stores)::int)::text, 3, '0'),
            round((power(random(), 3) * 6000 + 1)::numeric, 2), round((power(random(), 3) * 5000)::numeric, 2),
            'Pending', 100, NULL, 'Normal', now() + ((random() * 90 - 20) || ' hours')::interval,
            now() - ((random() * 14 * 24) || ' hours')::interval, now()
        FROM generate_series(1, :n) AS i
    """), {"n": cases, "stores": stores})
    await db.execute(text(f"""
        UPDATE cases SET priority = CASE WHEN total_exposure > 3000 THEN 'Critical' WHEN total_exposure > 1000 THEN 'High' ELSE 'Normal' END,
            investigation_status = CASE WHEN status = 'In Review' THEN 'Ready for Decision' WHEN status = 'Closed' THEN 'Resolved' ELSE 'Pending' END,
            evidence_completeness = CASE WHEN status IN ('Open') THEN 0 ELSE 100 END
        WHERE case_number LIKE 'ZS-{tag}-%'
    """))
    await db.commit()
    total = (await db.execute(select(func.count()).select_from(Case))).scalar()
    return {"created": cases, "tag": tag, "total_cases": total}


@router.delete("/scale-demo")
async def clear_scale_demo(db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("it"))):
    r = await db.execute(text("DELETE FROM cases WHERE case_number LIKE 'ZS-%'"))
    await db.commit()
    return {"deleted": r.rowcount}
