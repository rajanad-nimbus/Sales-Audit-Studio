"""Standard sales-audit reports, each paged on the server and downloadable as CSV.

Each report is read a page at a time (the database does the offset and limit), so a report over a
large volume of store days never loads more than one page into memory. There is deliberately no
total row count, since counting a whole range is itself a scan: a page says whether another follows.
A CSV download reads the same pages in turn, up to CSV_MAX_ROWS, and says when it stopped there.
"""

from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
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
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
CSV_CHUNK = 2000
CSV_MAX_ROWS = 100_000

Report = dict  # {"columns": [...], "rows": [[...]], "has_more": bool}


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


def page_of(found: list, limit: int) -> tuple[list, bool]:
    """The rows to show from a query that asked for one more than the page holds."""
    return found[:limit], len(found) > limit


async def variance_report(db: AsyncSession, user: dict, start, end, offset, limit) -> Report:
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
        .order_by(
            StoreDay.business_date.desc(), StoreDay.store_id, AuditTotal.total_name, AuditTotal.id
        )
        .offset(offset)
        .limit(limit + 1)
    )
    found, more = page_of(
        (await db.execute(scope.restrict(query, user, StoreDay.store_id))).all(), limit
    )
    days: dict[str, StoreDay] = {}
    totals: dict[str, list[AuditTotal]] = defaultdict(list)
    for day, total in found:
        days[day.id] = day
        totals[day.id].append(total)
    outcomes = await variances.evaluate_days(db, list(days.values()), totals)
    rows = []
    for day, total in found:
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
        "has_more": more,
    }


async def error_report(db: AsyncSession, user: dict, start, end, offset, limit) -> Report:
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
        .order_by(
            Case.store_id,
            func.count(ExceptionRow.id).desc(),
            ExceptionRow.exception_type,
            Case.currency,
        )
        .offset(offset)
        .limit(limit + 1)
    )
    found, more = page_of(
        (await db.execute(scope.restrict(query, user, Case.store_id))).all(), limit
    )
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
        for store, code, currency, count, open_count, amount in found
    ]
    return {
        "columns": ["Store", "Code", "Error", "Currency", "Errors", "Open", "Amount"],
        "rows": rows,
        "has_more": more,
    }


async def status_report(db: AsyncSession, user: dict, start, end, offset, limit) -> Report:
    """Where every store day stands: data, audit and export."""
    query = (
        select(StoreDay)
        .where(*date_filters(StoreDay.business_date, start, end))
        .order_by(StoreDay.business_date.desc(), StoreDay.store_id)
        .offset(offset)
        .limit(limit + 1)
    )
    found = list((await db.execute(scope.restrict(query, user, StoreDay.store_id))).scalars())
    days, more = page_of(found, limit)
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
        "has_more": more,
    }


async def tax_report(db: AsyncSession, user: dict, start, end, offset, limit) -> Report:
    """Tax collected by store, day and tax code."""
    group = (
        CanonicalTransaction.business_date,
        CanonicalTransaction.store_id,
        TransactionTax.tax_code,
        TransactionTax.tax_name,
        TransactionTax.rate,
        CanonicalTransaction.currency,
    )
    query = (
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
        .group_by(*group)
        .order_by(
            CanonicalTransaction.business_date.desc(),
            CanonicalTransaction.store_id,
            TransactionTax.tax_code,
            TransactionTax.tax_name,
            TransactionTax.rate,
            CanonicalTransaction.currency,
        )
        .offset(offset)
        .limit(limit + 1)
    )
    found, more = page_of(
        (await db.execute(scope.restrict(query, user, CanonicalTransaction.store_id))).all(), limit
    )
    return {
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
        "rows": [list(row) for row in found],
        "has_more": more,
    }


async def tax_correction_report(db: AsyncSession, user: dict, start, end, offset, limit) -> Report:
    """Approved corrections to a transaction's tax, one row each."""
    query = (
        select(
            CanonicalTransaction.store_id,
            CanonicalTransaction.business_date,
            CanonicalTransaction.id,
            CanonicalTransaction.currency,
            TransactionAdjustment.proposed_values,
            TransactionAdjustment.proposed_by,
            TransactionAdjustment.reviewed_by,
            TransactionAdjustment.reviewed_at,
            TransactionAdjustment.rationale,
        )
        .join(CanonicalTransaction, CanonicalTransaction.id == TransactionAdjustment.transaction_id)
        .where(
            TransactionAdjustment.adjustment_type == "Tax",
            TransactionAdjustment.status == "Approved",
            *date_filters(CanonicalTransaction.business_date, start, end),
        )
        .order_by(
            CanonicalTransaction.business_date.desc(),
            CanonicalTransaction.store_id,
            TransactionAdjustment.created_at,
            TransactionAdjustment.id,
        )
        .offset(offset)
        .limit(limit + 1)
    )
    found, more = page_of(
        (await db.execute(scope.restrict(query, user, CanonicalTransaction.store_id))).all(), limit
    )
    rows = []
    for (
        store,
        day,
        transaction_id,
        currency,
        values,
        proposed_by,
        reviewed_by,
        reviewed_at,
        why,
    ) in found:
        try:
            delta = cent_value((values or {}).get("tax_delta"), "tax_delta")
        except ValueError:
            delta = "Invalid"
        rows.append(
            [
                store,
                day,
                transaction_id,
                currency,
                delta,
                proposed_by,
                reviewed_by or "",
                reviewed_at,
                why,
            ]
        )
    return {
        "columns": [
            "Store",
            "Business date",
            "Transaction",
            "Currency",
            "Tax change",
            "Proposed by",
            "Approved by",
            "Approved at",
            "Reason",
        ],
        "rows": rows,
        "has_more": more,
    }


@dataclass(frozen=True)
class ReportSpec:
    title: str
    description: str
    build: Callable[..., Awaitable[Report]]


REPORTS: dict[str, ReportSpec] = {
    "variances": ReportSpec(
        "Total variances",
        "Totals that do not balance, and whether each is explained.",
        variance_report,
    ),
    "errors": ReportSpec(
        "Errors by store",
        "Audit errors by store and error code, with how many are open.",
        error_report,
    ),
    "store-days": ReportSpec(
        "Store day status", "Data, audit and export readiness for each store day.", status_report
    ),
    "tax": ReportSpec("Tax / VAT", "Tax collected by store, day and tax code.", tax_report),
    "tax-corrections": ReportSpec(
        "Tax corrections", "Approved corrections to a transaction's tax.", tax_correction_report
    ),
}


@router.get("")
async def list_reports(_: dict = Depends(require_role(*ROLES))):
    return [
        {"name": name, "title": spec.title, "description": spec.description}
        for name, spec in REPORTS.items()
    ]


@router.get("/{name}")
async def run_report(
    name: str,
    start: Annotated[str | None, Query(min_length=10, max_length=10)] = None,
    end: Annotated[str | None, Query(min_length=10, max_length=10)] = None,
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    page_size: Annotated[int, Query(ge=10, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    format: Annotated[str, Query(pattern="^(json|csv)$")] = "json",
    response: Response = None,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role(*ROLES)),
):
    spec = REPORTS.get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail="Unknown report")
    if format == "csv":
        return await download(spec, name, db, user, start, end)
    report = await spec.build(db, user, start, end, (page - 1) * page_size, page_size)
    response.headers["X-Has-More"] = "true" if report["has_more"] else "false"
    return {"title": spec.title, "page": page, "page_size": page_size, **report}


async def download(spec: ReportSpec, name: str, db, user, start, end) -> Response:
    """Read the report page by page into one CSV, stopping at CSV_MAX_ROWS."""
    columns: list = []
    rows: list = []
    offset, more = 0, True
    while more and len(rows) < CSV_MAX_ROWS:
        chunk = await spec.build(db, user, start, end, offset, CSV_CHUNK)
        columns = chunk["columns"]
        rows.extend(chunk["rows"])
        more, offset = chunk["has_more"], offset + CSV_CHUNK
    truncated = more and len(rows) >= CSV_MAX_ROWS
    return Response(
        csv_table(columns, rows[:CSV_MAX_ROWS]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={name}_{start or 'start'}_{end or 'now'}.csv",
            "X-Report-Truncated": "true" if truncated else "false",
        },
    )
