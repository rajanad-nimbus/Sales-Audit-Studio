"""Action Center: server-side triage at scale (filters, facets, aggregates, bulk actions, export)."""
import csv
import io
import math
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import and_, case, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.agents import investigation_service
from apps.search import contains_literal
from apps.security import scope
from integrations.ontology import nimbus
from integrations.ontology.ontology_integration import integration as ontology_integration
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
EXPORT_BATCH_SIZE = 1000
MAX_ACTION_CENTER_EXPORT_CASES = 50_000
MAX_FILTER_VALUES = 100
FILTER_VALUE_LENGTHS = {"status": 50, "case_type": 50, "store": 50, "priority": 20}
SORTS = {
    "sla": Case.sla_due_at, "exposure": Case.total_exposure, "amount": Case.total_exception_amount,
    "age": Case.created_at, "store": Case.store_id, "type": Case.case_type, "date": Case.business_date,
    "case_number": Case.case_number, "status": Case.status, "priority": Case.priority, "evidence": Case.evidence_completeness,
}
FILTER_FIELDS = frozenset({
    "q", "stage", "status", "case_type", "store", "priority", "assignee",
    "min_amount", "max_amount", "date_from", "date_to", "sla", "snoozed",
})


@dataclass(slots=True)
class Filters:
    q: str | None = None
    stage: str | None = None
    status: str | None = None
    case_type: str | None = None
    store: str | None = None
    priority: str | None = None
    assignee: str | None = None
    min_amount: float | None = None
    max_amount: float | None = None
    date_from: str | None = None
    date_to: str | None = None
    sla: str | None = None
    snoozed: str | None = None


def me_name(user: dict) -> str:
    return user["user"] or "demo.user"


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def apply(stmt, f: Filters, user: dict, skip: str | None = None):
    now = datetime.now(timezone.utc)
    conds = []
    csv_ = lambda v: [x.strip() for x in v.split(",") if x.strip()]
    if f.q:
        conds.append(or_(contains_literal(Case.case_number, f.q),
                         contains_literal(Case.store_id, f.q),
                         contains_literal(Case.case_type, f.q)))
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
    stmt = stmt.where(*conds) if conds else stmt
    return scope.restrict(stmt, user, Case.store_id)


def filters(q: str | None = None, stage: str | None = None, status: str | None = None, case_type: str | None = None,
            store: str | None = None, priority: str | None = None, assignee: str | None = None,
            min_amount: float | None = None, max_amount: float | None = None,
            date_from: Annotated[str | None, Query(min_length=10, max_length=10)] = None,
            date_to: Annotated[str | None, Query(min_length=10, max_length=10)] = None,
            sla: str | None = None, snoozed: str | None = None) -> Filters:
    for field, value in {
        "q": q,
        "stage": stage,
        "status": status,
        "case_type": case_type,
        "store": store,
        "priority": priority,
        "assignee": assignee,
        "sla": sla,
        "snoozed": snoozed,
    }.items():
        if value is None:
            continue
        values = [item.strip() for item in value.split(",") if item.strip()]
        if field in FILTER_VALUE_LENGTHS:
            max_length = FILTER_VALUE_LENGTHS[field]
            if len(values) > MAX_FILTER_VALUES or any(len(item) > max_length for item in values):
                raise HTTPException(
                    status_code=422,
                    detail=f"{field} supports at most {MAX_FILTER_VALUES} values of {max_length} characters",
                )
        elif len(value) > 255:
            raise HTTPException(status_code=422, detail=f"{field} must be at most 255 characters")
    date_from = _validated_filter_date(date_from, "date_from")
    date_to = _validated_filter_date(date_to, "date_to")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must not be after date_to")
    if stage not in (None, "", *STAGES):
        raise HTTPException(status_code=422, detail=f"stage must be one of: {', '.join(STAGES)}")
    if sla not in (None, "", "breached", "at_risk"):
        raise HTTPException(status_code=422, detail="sla must be breached or at_risk")
    if snoozed not in (None, "", "only", "all"):
        raise HTTPException(status_code=422, detail="snoozed must be only or all")
    if min_amount is not None and not math.isfinite(min_amount):
        raise HTTPException(status_code=422, detail="min_amount must be a finite number")
    if max_amount is not None and not math.isfinite(max_amount):
        raise HTTPException(status_code=422, detail="max_amount must be a finite number")
    if min_amount is not None and max_amount is not None and min_amount > max_amount:
        raise HTTPException(status_code=422, detail="min_amount must not exceed max_amount")
    return Filters(
        q=q, stage=stage, status=status, case_type=case_type, store=store,
        priority=priority, assignee=assignee, min_amount=min_amount, max_amount=max_amount,
        date_from=date_from, date_to=date_to, sla=sla, snoozed=snoozed,
    )


def _validated_filter_date(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    try:
        normalized = date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{field} must be a valid YYYY-MM-DD date") from exc
    if normalized != value:
        raise HTTPException(status_code=422, detail=f"{field} must use YYYY-MM-DD")
    return normalized


def sla_state(c: Case, now: datetime) -> str:
    if c.status == "Closed" or not c.sla_due_at:
        return "none"
    if as_utc(c.sla_due_at) < now:
        return "breached"
    return "at_risk" if as_utc(c.sla_due_at) < now + timedelta(hours=8) else "ok"


def row(c: Case, now: datetime) -> dict:
    return {"id": c.id, "case_number": c.case_number, "case_type": c.case_type, "store_id": c.store_id,
            "currency": c.currency,
            "amount_needs_review": c.amount_needs_review,
            "business_date": c.business_date, "status": c.status, "priority": c.priority,
            "amount": str(c.total_exception_amount), "exposure": str(c.total_exposure),
            "evidence": c.evidence_completeness, "investigation_status": c.investigation_status, "assigned_to": c.assigned_to,
            "sla_due_at": c.sla_due_at, "sla_state": sla_state(c, now), "snoozed_until": c.snoozed_until,
            "age_hours": int((now - as_utc(c.created_at)).total_seconds() // 3600)}


def first_record_per_case(model, case_ids: list[str], *conditions):
    """Select only each case's earliest matching child record in the database."""
    ranked = select(
        model.id.label("record_id"),
        func.row_number().over(
            partition_by=model.case_id,
            order_by=(model.created_at, model.id),
        ).label("row_number"),
    ).where(model.case_id.in_(case_ids), *conditions).subquery()
    return select(model).join(ranked, model.id == ranked.c.record_id).where(ranked.c.row_number == 1)


@router.get("/cases")
async def list_cases(f: Filters = Depends(filters),
                     sort: Literal["sla", "exposure", "amount", "age", "store", "type", "date",
                                   "case_number", "status", "priority", "evidence"] = "sla",
                     dir: Literal["asc", "desc"] = "asc",
                     page: int = Query(1, ge=1, le=10_000), page_size: int = Query(50, ge=1, le=100),
                     db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance", "it"))):
    now = datetime.now(timezone.utc)
    total = (await db.execute(apply(select(func.count()).select_from(Case), f, user))).scalar() or 0
    col = SORTS[sort]
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
    counter_conditions = {
        "needs_decision": and_(Case.status == "In Review", visible),
        "agent_prepared": and_(Case.investigation_status == "Ready for Decision", visible),
        "agent_waiting_evidence": and_(Case.investigation_status == "Awaiting Evidence", visible),
        "sla_breached": and_(live, Case.sla_due_at < now, visible),
        "at_risk": and_(live, Case.sla_due_at >= now, Case.sla_due_at < now + timedelta(hours=8), visible),
        "unassigned": and_(live, Case.assigned_to.is_(None), visible),
        "mine": and_(live, Case.assigned_to == me_name(user)),
        "snoozed": and_(live, Case.snoozed_until > now),
        "open_total": live,
        "stale_amount_cases": and_(live, Case.amount_needs_review.is_(True)),
        "closed": Case.status == "Closed",
    }
    counter_query = select(*(
        func.count(case((condition, 1))).label(name)
        for name, condition in counter_conditions.items()
    ), func.count(func.distinct(case((live, Case.store_id)))).label("stores_affected")).select_from(Case)
    counters = (await db.execute(scope.restrict(counter_query, user, Case.store_id))).mappings().one()
    manual_query = select(func.count(func.distinct(HumanDecision.case_id))).join(Case, Case.id == HumanDecision.case_id).where(
        HumanDecision.decision_type.in_(["Finance", "IT"]), Case.status == "Closed")
    manual = (await db.execute(scope.restrict(manual_query, user, Case.store_id))).scalar() or 0
    days_query = select(Case.business_date, func.count()).group_by(Case.business_date).order_by(Case.business_date.desc()).limit(14)
    days = (await db.execute(scope.restrict(days_query, user, Case.store_id))).all()
    currency_query = (select(Case.currency, func.sum(Case.total_exception_amount), func.sum(Case.total_exposure))
                      .where(live, Case.amount_needs_review.is_(False)).group_by(Case.currency))
    amounts = (await db.execute(scope.restrict(currency_query, user, Case.store_id))).all()
    amount_by_currency = [{"currency": currency, "amount": str(amount), "exposure": str(exposure)}
                          for currency, amount, exposure in amounts]
    single = amounts[0] if len(amounts) == 1 and amounts[0][0] else None
    return {
        **{name: counters[name] for name in counter_conditions if name != "closed"},
        "open_exposure": str(single[2]) if single else "0" if not amounts else None,
        "open_amount": str(single[1]) if single else "0" if not amounts else None,
        "currency": single[0] if single else None, "amounts_by_currency": amount_by_currency,
        "stores_affected": counters["stores_affected"],
        "closed": counters["closed"],
        "straight_through_rate": round(100 * max(counters["closed"] - manual, 0) / counters["closed"], 1) if counters["closed"] else None,
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
    top = (apply(select(col.label("k"), func.count().label("n")).group_by(col), f, user)
           .order_by(func.count().desc(), col).limit(100).subquery())
    stmt = apply(select(
        col.label("k"), Case.currency, func.count(),
        func.coalesce(func.sum(case((Case.amount_needs_review.is_(False), Case.total_exception_amount), else_=0)), 0),
        func.coalesce(func.sum(case((Case.amount_needs_review.is_(False), Case.total_exposure), else_=0)), 0),
        func.count().filter(and_(Case.status != "Closed", Case.sla_due_at < now)), func.min(Case.created_at),
        func.count().filter(Case.amount_needs_review.is_(True)),
    ).where(col.in_(select(top.c.k))).group_by(col, Case.currency), f, user)
    grouped = {}
    for key, currency, count, amount, exposure, breached, oldest, stale_count in (await db.execute(stmt)).all():
        item = grouped.setdefault(key, {"key": key, "count": 0, "breached": 0, "oldest": oldest, "stale_amount_cases": 0, "amounts_by_currency": []})
        item["count"] += count
        item["breached"] += breached
        item["stale_amount_cases"] += stale_count
        item["oldest"] = min(item["oldest"], oldest)
        item["amounts_by_currency"].append({"currency": currency, "amount": str(amount), "exposure": str(exposure)})
    result = []
    for item in grouped.values():
        amounts = item["amounts_by_currency"]
        single = amounts[0] if len(amounts) == 1 and amounts[0]["currency"] and not item["stale_amount_cases"] else None
        item["currency"] = single["currency"] if single else None
        item["amount"] = single["amount"] if single else None
        item["exposure"] = single["exposure"] if single else None
        item["oldest_hours"] = int((now - as_utc(item.pop("oldest"))).total_seconds() // 3600)
        result.append(item)
    return sorted(result, key=lambda item: (-item["count"], str(item["key"])))[:100]


class BulkRequest(BaseModel):
    action: Literal["assign", "unassign", "priority", "snooze", "unsnooze",
                    "investigate", "approve", "escalate", "advance"]
    ids: list[Annotated[str, Field(min_length=1, max_length=36)]] | None = Field(default=None, max_length=10_000)
    select_all: dict[str, object] | None = Field(default=None, max_length=20)
    params: dict = Field(default_factory=dict, max_length=5)

    @model_validator(mode="after")
    def validate_action_parameters(self):
        allowed_params = {
            "assign": {"assignee"},
            "priority": {"priority"},
            "snooze": {"hours"},
        }.get(self.action, set())
        unexpected_params = self.params.keys() - allowed_params
        if unexpected_params:
            raise ValueError(f"unsupported parameters for {self.action}: {', '.join(sorted(unexpected_params))}")

        if self.action == "snooze":
            hours = self.params.get("hours", 24)
            try:
                valid_hours = (
                    not isinstance(hours, bool)
                    and isinstance(hours, (int, float))
                    and math.isfinite(float(hours))
                    and 0 < hours <= 8760
                )
            except (OverflowError, ValueError):
                valid_hours = False
            if not valid_hours:
                raise ValueError("snooze hours must be greater than zero and no more than one year")
        if self.action == "assign":
            assignee = self.params.get("assignee", "me")
            if not isinstance(assignee, str) or not assignee.strip() or len(assignee) > 255:
                raise ValueError("assignee must be a nonempty string of at most 255 characters")
        if self.action == "priority":
            priority = self.params.get("priority")
            if not isinstance(priority, str) or priority not in nimbus.SLA_HOURS:
                raise ValueError("priority must be a supported priority")
        if self.select_all is not None:
            for key, value in self.select_all.items():
                if key not in FILTER_FIELDS:
                    raise ValueError(f"unsupported select_all filter: {key}")
                if len(key) > 50 or (isinstance(value, str) and len(value) > 500):
                    raise ValueError("select_all filter keys and values exceed the supported length")
                if value is not None and not isinstance(value, str):
                    raise ValueError(f"select_all filter {key} must be a string")
        return self


@router.post("/bulk")
async def bulk(req: BulkRequest, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance", "it"))):
    actor = me_name(user)
    if req.ids:
        stmt = select(Case).where(Case.id.in_(req.ids[:BULK_LIMIT]))
        truncated = len(req.ids) > BULK_LIMIT
    elif req.select_all is not None:
        stmt = apply(
            select(Case),
            filters(**req.select_all),
            user,
        )
        stmt = stmt.order_by(Case.sla_due_at.asc().nulls_last()).limit(BULK_LIMIT + 1)
        truncated = False
    else:
        raise HTTPException(status_code=400, detail="Provide ids or select_all")
    cases = list((await db.execute(stmt.order_by(Case.id).with_for_update().execution_options(populate_existing=True))).scalars().all())
    if req.ids:
        for case in cases:
            scope.require_store(user, case.store_id)
    if req.select_all is not None and len(cases) > BULK_LIMIT:
        cases, truncated = cases[:BULK_LIMIT], True

    heavy = req.action in ("investigate", "approve", "escalate", "advance")
    if heavy and len(cases) > HEAVY_LIMIT:
        cases, truncated = cases[:HEAVY_LIMIT], True

    exceptions_by_case: dict[str, list[DBException]] = {}
    if req.action == "investigate" and cases:
        exception_rows = (await db.execute(
            select(DBException).where(DBException.case_id.in_([case.id for case in cases]))
            .order_by(DBException.case_id, DBException.created_at, DBException.id)
        )).scalars()
        for exception in exception_rows:
            exceptions_by_case.setdefault(exception.case_id, []).append(exception)

    recommendations_by_case: dict[str, Recommendation] = {}
    if req.action in ("approve", "escalate") and cases:
        recommendations = (await db.execute(
            select(Recommendation).where(
                Recommendation.case_id.in_([case.id for case in cases]),
                Recommendation.status == "Proposed",
            ).order_by(Recommendation.case_id, Recommendation.created_at.desc(), Recommendation.id.desc())
        )).scalars()
        for recommendation in recommendations:
            recommendations_by_case.setdefault(recommendation.case_id, recommendation)

    pending_workflows_by_case: dict[str, Workflow] = {}
    incomplete_obligations_by_case: dict[str, ValidationObligation] = {}
    if req.action == "advance" and cases:
        resolving_case_ids = [case.id for case in cases if case.status == "Resolving"]
        if resolving_case_ids:
            pending_workflows = (await db.execute(
                first_record_per_case(Workflow, resolving_case_ids, Workflow.state == "Pending")
            )).scalars()
            for workflow in pending_workflows:
                pending_workflows_by_case[workflow.case_id] = workflow

        validation_case_ids = [case.id for case in cases if case.status == "Pending Validation"]
        if validation_case_ids:
            obligations = (await db.execute(
                first_record_per_case(
                    ValidationObligation,
                    validation_case_ids,
                    ValidationObligation.status != "Complete",
                )
            )).scalars()
            for obligation in obligations:
                incomplete_obligations_by_case[obligation.case_id] = obligation

    validation_workflows: dict[str, Workflow] = {}
    obligation_workflow_ids = {obligation.workflow_id for obligation in incomplete_obligations_by_case.values()}
    if req.action == "advance" and obligation_workflow_ids:
        validation_workflows = {
            workflow.id: workflow
            for workflow in (await db.execute(
                select(Workflow).where(Workflow.id.in_(obligation_workflow_ids))
            )).scalars()
        }

    approved_rule_codes = None
    ok, skipped = 0, []
    evidence_cache = investigation_service.InvestigationEvidenceCache(db)
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
            if c.amount_needs_review: skip(c, "case amounts require finance review"); continue
            if c.status not in ("Open", "In Investigation"): skip(c, f"already {c.status}"); continue
            excs = exceptions_by_case.get(c.id, [])
            if not excs: skip(c, "no exception records to investigate"); continue
            await investigation_service.run_investigation(db, c, excs, evidence_cache=evidence_cache); ok += 1
        elif a in ("approve", "escalate"):
            if user["role"] not in ("finance", "admin"): skip(c, "requires finance role"); continue
            if c.status != "In Review": skip(c, f"not awaiting decision ({c.status})"); continue
            rec = recommendations_by_case.get(c.id)
            if not rec: skip(c, "no open recommendation"); continue
            if a == "approve":
                if rec.financial_impact != 0 and c.currency != nimbus.THRESHOLD_CURRENCY:
                    skip(c, "bulk financial limit is in USD; review this currency individually"); continue
                if abs(rec.financial_impact) > BULK_APPROVE_MAX_IMPACT:
                    skip(c, f"financial movement above bulk limit ({BULK_APPROVE_MAX_IMPACT}); review individually"); continue
                if approved_rule_codes is None:
                    approved_rule_codes = await ontology_integration.refresh()
                if nimbus.evaluate_policy(db, c, rec, approved_rule_codes).outcome == "RequestEvidence":
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
                wf = pending_workflows_by_case.get(c.id)
                if wf: nimbus.resolve(db, c, wf); ok += 1
                else: skip(c, "no pending workflow")
            elif c.status == "Pending Validation":
                ob = incomplete_obligations_by_case.get(c.id)
                if ob:
                    wf = validation_workflows.get(ob.workflow_id)
                    if wf is None:
                        raise HTTPException(status_code=409, detail="Validation workflow no longer exists")
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
    matching_cases = (await db.execute(
        apply(select(func.count()).select_from(Case), f, user)
    )).scalar_one()
    if matching_cases > MAX_ACTION_CENTER_EXPORT_CASES:
        raise HTTPException(
            status_code=413,
            detail=(f"Export contains {matching_cases:,} cases; the limit is "
                    f"{MAX_ACTION_CENTER_EXPORT_CASES:,}. Narrow the filters."),
        )

    col = SORTS.get(sort, Case.sla_due_at)
    cols = ["case_number", "case_type", "store_id", "business_date", "status", "priority", "amount", "exposure",
            "evidence", "assigned_to", "sla_due_at", "sla_state", "age_hours"]

    async def generate():
        now = datetime.now(timezone.utc)
        descending = dir.lower() == "desc"

        def csv_line(values):
            buffer = io.StringIO()
            csv.writer(buffer).writerow(values)
            return buffer.getvalue()

        yield csv_line(cols)
        ordering = col.desc().nulls_last() if descending else col.asc().nulls_last()
        stmt = apply(select(Case), f, user).order_by(ordering, Case.id).limit(MAX_ACTION_CENTER_EXPORT_CASES)
        result = await db.stream(stmt.execution_options(yield_per=EXPORT_BATCH_SIZE))
        try:
            async for case_row in result.scalars():
                values = row(case_row, now)
                yield csv_line([values[key] for key in cols])
        finally:
            await result.close()

    return StreamingResponse(generate(), media_type="text/csv",
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
