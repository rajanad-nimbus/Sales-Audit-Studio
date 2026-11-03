"""Standard sales-audit reports, each shown as a table and downloadable as CSV."""

from collections import defaultdict
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Annotated

from database import get_db
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from integrations.ontology import ontology
from sqlalchemy import case as sql_case
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.cases.models import Case
from apps.cases.models import Exception as ExceptionRow
from apps.corrections.models import TransactionAdjustment
from apps.csv_export import csv_table
from apps.ingest.manifests import validate_business_date
from apps.security import scope
from apps.security.authentication import require_role
from apps.store_days import variances
from apps.store_days.models import AuditTotal, StoreDay
from apps.store_days.readiness import readiness
from apps.transactions.detail import cent_value
from apps.transactions.models import CanonicalTransaction, TransactionTax

router = APIRouter(prefix="/api/reports", tags=["Reports"])
ROLES = ("analyst", "finance", "it", "auditor")
MAX_ROWS = 5000

Report = dict  # {"title", "columns": [...], "rows": [[...]], "truncated": bool}


def date_filters(column, start: str | None, end: str | None) -> list:
    filters = []
    for value, operator in ((start, "ge"), (end, "le")):
        if not value:
            continue
        try:
            validate_business_date(value)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        filters.append(column >= value if operator == "ge" else column <= value)
    return filters


async def variance_report(db: AsyncSession, user: dict, start, end) -> Report:
    """Every total with a variance on a store day's current version, and how it stands."""
    query = (
        select(StoreDay, AuditTotal)
        .join(
            AuditTotal,
            (AuditTotal.store_day_id == StoreDay.id)
            & (AuditTotal.audit_version == StoreDay.audit_version),
        )
        .where(
            AuditTotal.variance_amount.is_not(None),
            AuditTotal.variance_amount != 0,
            *date_filters(StoreDay.business_date, start, end),
        )
        .order_by(StoreDay.business_date.desc(), StoreDay.store_id, AuditTotal.total_name)
        .limit(MAX_ROWS + 1)
    )
    found = (await db.execute(scope.restrict(query, user, StoreDay.store_id))).all()
    days: dict[str, StoreDay] = {}
    totals: dict[str, list[AuditTotal]] = defaultdict(list)
    for day, total in found[:MAX_ROWS]:
        days[day.id] = day
        totals[day.id].append(total)
    outcomes = await variances.evaluate_days(db, list(days.values()), totals)
    rows = []
    for day, total in found[:MAX_ROWS]:
        evaluation = outcomes[day.id][total.id]
        explanation = evaluation["explanation"]
        rows.append(
            [
                day.store_id,
                day.business_date,
                total.total_name,
                total.level,
                total.dimension_key or "",
                total.calculated_amount,
                total.declared_amount,
                total.variance_amount,
                total.currency,
                evaluation["outcome"],
                "Yes" if evaluation["resolved"] else "No",
                explanation["reason"] if explanation else "",
            ]
        )
    return {
        "title": "Total variances",
        "columns": [
            "Store",
            "Business date",
            "Total",
            "Level",
            "Dimension",
            "Calculated",
            "Declared",
            "Variance",
            "Currency",
            "Tolerance outcome",
            "Resolved",
            "Explanation",
        ],
        "rows": rows,
        "truncated": len(found) > MAX_ROWS,
    }


async def error_report(db: AsyncSession, user: dict, start, end) -> Report:
    """Audit errors by store and error code, with how many are still open."""
    is_open = sql_case((Case.status != "Closed", 1), else_=0)
    query = (
        select(
            Case.store_id,
            ExceptionRow.exception_type,
            Case.currency,
            func.count(ExceptionRow.id),
            func.sum(is_open),
            func.coalesce(func.sum(ExceptionRow.exception_amount), 0),
        )
        .join(Case, Case.id == ExceptionRow.case_id)
        .where(*date_filters(Case.business_date, start, end))
        .group_by(Case.store_id, ExceptionRow.exception_type, Case.currency)
        .order_by(Case.store_id, func.count(ExceptionRow.id).desc(), ExceptionRow.exception_type)
        .limit(MAX_ROWS + 1)
    )
    found = (await db.execute(scope.restrict(query, user, Case.store_id))).all()
    rows = [
        [
            store,
            code,
            (ontology.EXCEPTION_TYPES.get(code) or {}).get("label") or code,
            currency or "",
            count,
            open_count or 0,
            amount,
        ]
        for store, code, currency, count, open_count, amount in found[:MAX_ROWS]
    ]
    return {
        "title": "Audit errors by store",
        "columns": ["Store", "Code", "Error", "Currency", "Errors", "Open", "Amount"],
        "rows": rows,
        "truncated": len(found) > MAX_ROWS,
    }


async def status_report(db: AsyncSession, user: dict, start, end) -> Report:
    """Where every store day stands: data, audit and export."""
    query = (
        select(StoreDay)
        .where(*date_filters(StoreDay.business_date, start, end))
        .order_by(StoreDay.business_date.desc(), StoreDay.store_id)
        .limit(MAX_ROWS + 1)
    )
    found = list((await db.execute(scope.restrict(query, user, StoreDay.store_id))).scalars())
    days = found[:MAX_ROWS]
    views = await readiness(db, days)
    rows = [
        [
            day.store_id,
            day.business_date,
            day.status,
            day.audit_version,
            views[day.id]["ingest"]["state"],
            views[day.id]["audit"]["state"],
            views[day.id]["audit"]["open_cases"],
            views[day.id]["audit"]["unexplained_variances"],
            views[day.id]["export"]["state"],
            day.closed_by or "",
        ]
        for day in days
    ]
    return {
        "title": "Store day status",
        "columns": [
            "Store",
            "Business date",
            "Status",
            "Totals version",
            "Data",
            "Audit",
            "Open cases",
            "Unexplained variances",
            "Export",
            "Closed by",
        ],
        "rows": rows,
        "truncated": len(found) > MAX_ROWS,
    }


async def tax_report(db: AsyncSession, user: dict, start, end) -> Report:
    """Tax collected by store, day and tax code, with approved tax corrections listed separately."""
    recorded = (
        select(
            CanonicalTransaction.store_id,
            CanonicalTransaction.business_date,
            TransactionTax.tax_code,
            TransactionTax.tax_name,
            TransactionTax.rate,
            CanonicalTransaction.currency,
            func.count(func.distinct(CanonicalTransaction.id)),
            func.coalesce(func.sum(TransactionTax.taxable_amount), 0),
            func.coalesce(func.sum(TransactionTax.tax_amount), 0),
        )
        .join(CanonicalTransaction, CanonicalTransaction.id == TransactionTax.transaction_id)
        .where(*date_filters(CanonicalTransaction.business_date, start, end))
        .group_by(
            CanonicalTransaction.store_id,
            CanonicalTransaction.business_date,
            TransactionTax.tax_code,
            TransactionTax.tax_name,
            TransactionTax.rate,
            CanonicalTransaction.currency,
        )
        .order_by(
            CanonicalTransaction.business_date.desc(),
            CanonicalTransaction.store_id,
            TransactionTax.tax_code,
        )
        .limit(MAX_ROWS + 1)
    )
    found = (await db.execute(scope.restrict(recorded, user, CanonicalTransaction.store_id))).all()
    rows = [
        [store, day, code, name, rate, currency, count, taxable, tax]
        for store, day, code, name, rate, currency, count, taxable, tax in found[:MAX_ROWS]
    ]

    adjustments = (
        select(
            CanonicalTransaction.store_id,
            CanonicalTransaction.business_date,
            CanonicalTransaction.currency,
            TransactionAdjustment.proposed_values,
        )
        .join(CanonicalTransaction, CanonicalTransaction.id == TransactionAdjustment.transaction_id)
        .where(
            TransactionAdjustment.adjustment_type == "Tax",
            TransactionAdjustment.status == "Approved",
            *date_filters(CanonicalTransaction.business_date, start, end),
        )
        .limit(MAX_ROWS)
    )
    deltas: dict[tuple[str, str, str], list] = defaultdict(lambda: [0, Decimal("0")])
    for store, day, currency, values in (
        await db.execute(scope.restrict(adjustments, user, CanonicalTransaction.store_id))
    ).all():
        try:
            delta = cent_value((values or {}).get("tax_delta"), "tax_delta")
        except ValueError:
            continue
        deltas[(store, day, currency)][0] += 1
        deltas[(store, day, currency)][1] += delta
    for (store, day, currency), (count, delta) in sorted(deltas.items(), reverse=True):
        rows.append([store, day, "ADJ", "Approved tax corrections", "", currency, count, "", delta])
    return {
        "title": "Tax by store and tax code",
        "columns": [
            "Store",
            "Business date",
            "Tax code",
            "Tax",
            "Rate",
            "Currency",
            "Transactions",
            "Taxable amount",
            "Tax amount",
        ],
        "rows": rows,
        "truncated": len(found) > MAX_ROWS,
    }


REPORTS: dict[str, Callable[..., Awaitable[Report]]] = {
    "variances": variance_report,
    "errors": error_report,
    "store-days": status_report,
    "tax": tax_report,
}


@router.get("")
async def list_reports(_: dict = Depends(require_role(*ROLES))):
    return [{"name": name, "title": fn.__doc__.split("\n")[0]} for name, fn in REPORTS.items()]


@router.get("/{name}")
async def run_report(
    name: str,
    start: Annotated[str | None, Query(min_length=10, max_length=10)] = None,
    end: Annotated[str | None, Query(min_length=10, max_length=10)] = None,
    format: Annotated[str, Query(pattern="^(json|csv)$")] = "json",
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role(*ROLES)),
):
    builder = REPORTS.get(name)
    if builder is None:
        raise HTTPException(status_code=404, detail="Unknown report")
    report = await builder(db, user, start, end)
    if format == "csv":
        suffix = f"_{start or 'start'}_{end or 'now'}"
        return Response(
            csv_table(report["columns"], report["rows"]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={name}{suffix}.csv"},
        )
    return report
