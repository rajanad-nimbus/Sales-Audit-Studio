"""Validated Action Center filters and their reusable SQL predicates."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import HTTPException, Query
from sqlalchemy import and_, or_

from apps.cases.models import Case
from apps.search import contains_literal
from apps.security import scope

STAGES = {
    "open": ["In Review", "Open", "In Investigation", "Resolving", "Pending Validation"],
    "decision": ["In Review"],
    "progress": ["Open", "In Investigation", "Resolving", "Pending Validation"],
    "done": ["Closed", "Escalated"],
}


MAX_FILTER_VALUES = 100


FILTER_VALUE_LENGTHS = {"status": 50, "case_type": 50, "store": 50, "priority": 20}


SORTS = {
    "sla": Case.sla_due_at,
    "exposure": Case.total_exposure,
    "amount": Case.total_exception_amount,
    "age": Case.created_at,
    "store": Case.store_id,
    "type": Case.case_type,
    "date": Case.business_date,
    "case_number": Case.case_number,
    "status": Case.status,
    "priority": Case.priority,
    "evidence": Case.evidence_completeness,
}


FILTER_FIELDS = frozenset(
    {
        "q",
        "stage",
        "status",
        "case_type",
        "store",
        "priority",
        "assignee",
        "min_amount",
        "max_amount",
        "date_from",
        "date_to",
        "sla",
        "snoozed",
    }
)


@dataclass(slots=True)
class Filters:
    q: str | None = None
    stage: str | None = None
    status: str | None = None
    case_type: str | None = None
    store: str | None = None
    priority: str | None = None
    assignee: str | None = None
    min_amount: Decimal | None = None
    max_amount: Decimal | None = None
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
        conditions.append(
            or_(
                contains_literal(Case.case_number, f.q),
                contains_literal(Case.store_id, f.q),
                contains_literal(Case.case_type, f.q),
            )
        )
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
            conditions.append(
                Case.assigned_to == (me_name(user) if f.assignee == "me" else f.assignee)
            )
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
        conditions.append(
            and_(
                Case.status != "Closed",
                Case.sla_due_at >= now,
                Case.sla_due_at < now + timedelta(hours=8),
            )
        )
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


def _normalize_amount_filter(value: Decimal | None, field: str) -> Decimal | None:
    if value is None:
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"{field} must be a decimal amount") from exc
    if not amount.is_finite():
        raise HTTPException(status_code=422, detail=f"{field} must be a finite number")
    return amount


def _validate_amount_filters(min_amount: Decimal | None, max_amount: Decimal | None) -> None:
    if min_amount is not None and not min_amount.is_finite():
        raise HTTPException(status_code=422, detail="min_amount must be a finite number")
    if max_amount is not None and not max_amount.is_finite():
        raise HTTPException(status_code=422, detail="max_amount must be a finite number")
    if min_amount is not None and max_amount is not None and min_amount > max_amount:
        raise HTTPException(status_code=422, detail="min_amount must not exceed max_amount")


def filters(
    q: str | None = None,
    stage: str | None = None,
    status: str | None = None,
    case_type: str | None = None,
    store: str | None = None,
    priority: str | None = None,
    assignee: str | None = None,
    min_amount: Annotated[Decimal | None, Query(max_digits=15, decimal_places=2)] = None,
    max_amount: Annotated[Decimal | None, Query(max_digits=15, decimal_places=2)] = None,
    date_from: Annotated[str | None, Query(min_length=10, max_length=10)] = None,
    date_to: Annotated[str | None, Query(min_length=10, max_length=10)] = None,
    sla: str | None = None,
    snoozed: str | None = None,
) -> Filters:
    _validate_text_filters(
        {
            "q": q,
            "stage": stage,
            "status": status,
            "case_type": case_type,
            "store": store,
            "priority": priority,
            "assignee": assignee,
            "sla": sla,
            "snoozed": snoozed,
        }
    )
    date_from = _validated_filter_date(date_from, "date_from")
    date_to = _validated_filter_date(date_to, "date_to")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must not be after date_to")
    _validate_filter_options(stage, sla, snoozed)
    min_amount = _normalize_amount_filter(min_amount, "min_amount")
    max_amount = _normalize_amount_filter(max_amount, "max_amount")
    _validate_amount_filters(min_amount, max_amount)
    return Filters(
        q=q,
        stage=stage,
        status=status,
        case_type=case_type,
        store=store,
        priority=priority,
        assignee=assignee,
        min_amount=min_amount,
        max_amount=max_amount,
        date_from=date_from,
        date_to=date_to,
        sla=sla,
        snoozed=snoozed,
    )


def _validated_filter_date(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    try:
        normalized = date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"{field} must be a valid YYYY-MM-DD date"
        ) from exc
    if normalized != value:
        raise HTTPException(status_code=422, detail=f"{field} must use YYYY-MM-DD")
    return normalized
