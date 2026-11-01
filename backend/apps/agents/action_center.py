"""Action Center: server-side triage at scale (filters, facets, aggregates, bulk actions, export)."""
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal

from database import get_db
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from integrations.ontology import nimbus
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.agents import bulk_actions
from apps.agents.bulk_actions import BULK_LIMIT, HEAVY_LIMIT
from apps.cases.models import (
    Case,
    HumanDecision,
)
from apps.csv_export import csv_row
from apps.search import contains_literal
from apps.security import scope
from apps.security.authentication import require_role

router = APIRouter(prefix="/api/action-center")

STAGES = {
    "open": ["In Review", "Open", "In Investigation", "Resolving", "Pending Validation"],
    "decision": ["In Review"],
    "progress": ["Open", "In Investigation", "Resolving", "Pending Validation"],
    "done": ["Closed", "Escalated"],
}
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
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def apply(stmt, f: Filters, user: dict, skip: str | None = None):
    now = datetime.now(UTC)
    conditions = (
        *_text_filter_conditions(f, user, skip),
        *_amount_date_conditions(f),
        *_schedule_conditions(f, now),
    )
    stmt = stmt.where(*conditions) if conditions else stmt
    return scope.restrict(stmt, user, Case.store_id)


def _text_filter_conditions(f: Filters, user: dict, skip: str | None) -> list:
    conditions = []
    if f.q:
        conditions.append(or_(contains_literal(Case.case_number, f.q),
                              contains_literal(Case.store_id, f.q),
                              contains_literal(Case.case_type, f.q)))
    if f.stage and skip != "stage" and f.stage in STAGES:
        conditions.append(Case.status.in_(STAGES[f.stage]))
    if f.status and skip != "status":
        conditions.append(Case.status.in_(_csv_values(f.status)))
    if f.case_type and skip != "case_type":
        conditions.append(Case.case_type.in_(_csv_values(f.case_type)))
    if f.store and skip != "store":
        conditions.append(Case.store_id.in_(_csv_values(f.store)))
    if f.priority and skip != "priority":
        conditions.append(Case.priority.in_(_csv_values(f.priority)))
    if f.assignee:
        if f.assignee == "unassigned":
            conditions.append(Case.assigned_to.is_(None))
        else:
            conditions.append(Case.assigned_to == (me_name(user) if f.assignee == "me" else f.assignee))
    return conditions


def _csv_values(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _amount_date_conditions(f: Filters) -> list:
    conditions = []
    if f.min_amount is not None:
        conditions.append(Case.total_exception_amount >= f.min_amount)
    if f.max_amount is not None:
        conditions.append(Case.total_exception_amount <= f.max_amount)
    if f.date_from:
        conditions.append(Case.business_date >= f.date_from)
    if f.date_to:
        conditions.append(Case.business_date <= f.date_to)
    return conditions


def _schedule_conditions(f: Filters, now: datetime) -> list:
    conditions = []
    if f.sla == "breached":
        conditions.append(and_(Case.status != "Closed", Case.sla_due_at < now))
    elif f.sla == "at_risk":
        conditions.append(and_(Case.status != "Closed", Case.sla_due_at >= now,
                               Case.sla_due_at < now + timedelta(hours=8)))
    if f.snoozed == "only":
        conditions.append(Case.snoozed_until > now)
    elif f.snoozed != "all":
        conditions.append(or_(Case.snoozed_until.is_(None), Case.snoozed_until <= now))
    return conditions


def _validate_text_filters(values: dict[str, str | None]) -> None:
    for field, value in values.items():
        if value is None:
            continue
        parts = _csv_values(value)
        if field in FILTER_VALUE_LENGTHS:
            max_length = FILTER_VALUE_LENGTHS[field]
            if len(parts) > MAX_FILTER_VALUES or any(len(item) > max_length for item in parts):
                raise HTTPException(
                    status_code=422,
                    detail=f"{field} supports at most {MAX_FILTER_VALUES} values of {max_length} characters",
                )
        elif len(value) > 255:
            raise HTTPException(status_code=422, detail=f"{field} must be at most 255 characters")


def _validate_filter_options(stage: str | None, sla: str | None, snoozed: str | None) -> None:
    if stage not in (None, "", *STAGES):
        raise HTTPException(status_code=422, detail=f"stage must be one of: {', '.join(STAGES)}")
    if sla not in (None, "", "breached", "at_risk"):
        raise HTTPException(status_code=422, detail="sla must be breached or at_risk")
    if snoozed not in (None, "", "only", "all"):
        raise HTTPException(status_code=422, detail="snoozed must be only or all")


def _validate_amount_filters(min_amount: float | None, max_amount: float | None) -> None:
    if min_amount is not None and not math.isfinite(min_amount):
        raise HTTPException(status_code=422, detail="min_amount must be a finite number")
    if max_amount is not None and not math.isfinite(max_amount):
        raise HTTPException(status_code=422, detail="max_amount must be a finite number")
    if min_amount is not None and max_amount is not None and min_amount > max_amount:
        raise HTTPException(status_code=422, detail="min_amount must not exceed max_amount")


def filters(q: str | None = None, stage: str | None = None, status: str | None = None, case_type: str | None = None,
            store: str | None = None, priority: str | None = None, assignee: str | None = None,
            min_amount: float | None = None, max_amount: float | None = None,
            date_from: Annotated[str | None, Query(min_length=10, max_length=10)] = None,
            date_to: Annotated[str | None, Query(min_length=10, max_length=10)] = None,
            sla: str | None = None, snoozed: str | None = None) -> Filters:
    _validate_text_filters({
        "q": q,
        "stage": stage,
        "status": status,
        "case_type": case_type,
        "store": store,
        "priority": priority,
        "assignee": assignee,
        "sla": sla,
        "snoozed": snoozed,
    })
    date_from = _validated_filter_date(date_from, "date_from")
    date_to = _validated_filter_date(date_to, "date_to")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must not be after date_to")
    _validate_filter_options(stage, sla, snoozed)
    _validate_amount_filters(min_amount, max_amount)
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


@router.get("/cases")
async def list_cases(f: Filters = Depends(filters),
                     sort: Literal["sla", "exposure", "amount", "age", "store", "type", "date",
                                   "case_number", "status", "priority", "evidence"] = "sla",
                     dir: Literal["asc", "desc"] = "asc",
                     page: int = Query(1, ge=1, le=10_000), page_size: int = Query(50, ge=1, le=100),
                     db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance", "it"))):
    now = datetime.now(UTC)
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
    now = datetime.now(UTC)
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
    now = datetime.now(UTC)
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
        _validate_bulk_action_params(self.action, self.params)
        if self.select_all is not None:
            _validate_select_all_filters(self.select_all)
        return self


def _validate_bulk_action_params(action: str, params: dict) -> None:
    allowed_params = {
        "assign": {"assignee"},
        "priority": {"priority"},
        "snooze": {"hours"},
    }.get(action, set())
    unexpected_params = params.keys() - allowed_params
    if unexpected_params:
        raise ValueError(f"unsupported parameters for {action}: {', '.join(sorted(unexpected_params))}")

    if action == "snooze":
        _validate_snooze_hours(params.get("hours", 24))
    elif action == "assign":
        assignee = params.get("assignee", "me")
        if not isinstance(assignee, str) or not assignee.strip() or len(assignee) > 255:
            raise ValueError("assignee must be a nonempty string of at most 255 characters")
    elif action == "priority":
        priority = params.get("priority")
        if not isinstance(priority, str) or priority not in nimbus.SLA_HOURS:
            raise ValueError("priority must be a supported priority")


def _validate_snooze_hours(hours: object) -> None:
    try:
        valid = (
            not isinstance(hours, bool)
            and isinstance(hours, (int, float))
            and math.isfinite(float(hours))
            and 0 < hours <= 8760
        )
    except (OverflowError, ValueError):
        valid = False
    if not valid:
        raise ValueError("snooze hours must be greater than zero and no more than one year")


def _validate_select_all_filters(filters: dict[str, object]) -> None:
    for key, value in filters.items():
        if key not in FILTER_FIELDS:
            raise ValueError(f"unsupported select_all filter: {key}")
        if len(key) > 50 or (isinstance(value, str) and len(value) > 500):
            raise ValueError("select_all filter keys and values exceed the supported length")
        if value is not None and not isinstance(value, str):
            raise ValueError(f"select_all filter {key} must be a string")


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
    cases = list((await db.execute(
        stmt.order_by(Case.id).with_for_update().execution_options(populate_existing=True)
    )).scalars().all())
    if req.ids:
        for case in cases:
            scope.require_store(user, case.store_id)
    if req.select_all is not None and len(cases) > BULK_LIMIT:
        cases, truncated = cases[:BULK_LIMIT], True

    heavy = req.action in {"investigate", "approve", "escalate", "advance"}
    if heavy and len(cases) > HEAVY_LIMIT:
        cases, truncated = cases[:HEAVY_LIMIT], True
    return await bulk_actions.execute_bulk_action(
        db, req.action, req.params, cases, user, actor, truncated,
    )


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
        now = datetime.now(UTC)
        descending = dir.lower() == "desc"

        yield csv_row(cols)
        ordering = col.desc().nulls_last() if descending else col.asc().nulls_last()
        stmt = apply(select(Case), f, user).order_by(ordering, Case.id).limit(MAX_ACTION_CENTER_EXPORT_CASES)
        result = await db.stream(stmt.execution_options(yield_per=EXPORT_BATCH_SIZE))
        try:
            async for case_row in result.scalars():
                values = row(case_row, now)
                yield csv_row(values[key] for key in cols)
        finally:
            await result.close()

    return StreamingResponse(generate(), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=action-center.csv"})
