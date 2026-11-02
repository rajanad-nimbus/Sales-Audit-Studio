"""Audit error overview: every Sales Audit error type, how many are open, and how to fix them."""

from collections import defaultdict
from decimal import Decimal
from typing import Annotated

from database import get_db
from fastapi import APIRouter, Depends, Query
from integrations.ontology import ontology
from sqlalchemy import case as sql_case
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.cases.models import Case
from apps.cases.models import Exception as ExceptionRow
from apps.security import scope
from apps.security.authentication import require_role
from apps.totals.models import SalesAuditErrorDefinition

router = APIRouter(prefix="/api/audit-errors", tags=["Audit errors"])

ROLES = ("analyst", "finance", "it", "auditor")
MAX_CASES = 100


def definition_text(definition: SalesAuditErrorDefinition | None) -> dict:
    """The catalog's wording and where the error is fixed, when the catalog knows this code."""
    if definition is None:
        return {"description": None, "form": None, "tab": None, "recommended_solution": None}
    return {
        "description": definition.description,
        "form": definition.form,
        "tab": definition.tab,
        "recommended_solution": definition.recommended_solution,
    }


def ontology_text(code: str) -> dict:
    """The ontology's label, family, causes and resolution steps for this exception type."""
    meta = ontology.EXCEPTION_TYPES.get(code)
    if meta is None:
        return {
            "label": None,
            "family": None,
            "known_causes": [],
            "resolution": None,
            "steps": None,
        }
    kind, _handling, _exposure, steps = meta["disposition"]
    return {
        "label": meta["label"],
        "family": meta["family"],
        "known_causes": meta["known_causes"],
        "resolution": kind,
        "steps": steps,
    }


@router.get("")
async def overview(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role(*ROLES)),
):
    """One row per error type: the full catalog, with the open and total counts raised so far."""
    is_open = sql_case((Case.status != "Closed", 1), else_=0)
    counts = (
        select(
            ExceptionRow.exception_type,
            Case.currency,
            is_open.label("open"),
            func.count(ExceptionRow.id),
            func.coalesce(func.sum(ExceptionRow.exception_amount), 0),
            func.coalesce(func.sum(ExceptionRow.estimated_exposure), 0),
            func.max(ExceptionRow.created_at),
        )
        .join(Case, Case.id == ExceptionRow.case_id)
        .group_by(ExceptionRow.exception_type, Case.currency, is_open)
    )
    rows = (await db.execute(scope.restrict(counts, user, Case.store_id))).all()

    stats: dict[str, dict] = defaultdict(
        lambda: {"open": 0, "total": 0, "latest": None, "by_currency": defaultdict(lambda: [0, 0])}
    )
    for code, currency, open_flag, count, amount, exposure, latest in rows:
        stat = stats[code]
        stat["total"] += count
        stat["latest"] = max(filter(None, [stat["latest"], latest]), default=None)
        if open_flag:
            stat["open"] += count
            by_currency = stat["by_currency"][currency]
            by_currency[0] += Decimal(str(amount))
            by_currency[1] += Decimal(str(exposure))

    catalog = {
        d.code: d
        for d in (
            await db.execute(
                select(SalesAuditErrorDefinition).where(SalesAuditErrorDefinition.active.is_(True))
            )
        )
        .scalars()
        .all()
    }
    items = []
    for code in sorted(set(ontology.EXCEPTION_TYPES) | set(catalog) | set(stats)):
        text, meta, stat = definition_text(catalog.get(code)), ontology_text(code), stats.get(code)
        solution = text["recommended_solution"] or meta["steps"]
        items.append(
            {
                "code": code,
                "label": meta["label"]
                or (catalog[code].short_description if code in catalog else code),
                "family": meta["family"] or text["form"] or "Other",
                **text,
                "recommended_solution": solution,
                "known_causes": meta["known_causes"],
                "resolution": meta["resolution"],
                "open_count": stat["open"] if stat else 0,
                "total_count": stat["total"] if stat else 0,
                "latest_at": stat["latest"].isoformat() if stat and stat["latest"] else None,
                "open_by_currency": [
                    {"currency": currency, "amount": str(amount), "exposure": str(exposure)}
                    for currency, (amount, exposure) in sorted(
                        (stat["by_currency"] if stat else {}).items(), key=lambda kv: str(kv[0])
                    )
                ],
            }
        )
    items.sort(key=lambda i: (-i["open_count"], -i["total_count"], i["code"]))
    return {
        "items": items,
        "open_total": sum(i["open_count"] for i in items),
        "open_types": sum(1 for i in items if i["open_count"]),
    }


@router.get("/{code}/cases")
async def cases_for_error(
    code: str,
    limit: Annotated[int, Query(ge=1, le=MAX_CASES)] = 25,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role(*ROLES)),
):
    """The cases holding this error type, open ones first, newest first."""
    query = (
        select(Case, func.count(ExceptionRow.id))
        .join(ExceptionRow, ExceptionRow.case_id == Case.id)
        .where(ExceptionRow.exception_type == code)
        .group_by(Case.id)
        .order_by(sql_case((Case.status != "Closed", 0), else_=1), Case.created_at.desc(), Case.id)
        .limit(limit + 1)
    )
    rows = (await db.execute(scope.restrict(query, user, Case.store_id))).all()
    return {
        "items": [
            {
                "id": c.id,
                "case_number": c.case_number,
                "status": c.status,
                "store_id": c.store_id,
                "business_date": c.business_date,
                "amount": str(c.total_exception_amount),
                "currency": c.currency,
                "priority": c.priority,
                "errors": errors,
            }
            for c, errors in rows[:limit]
        ],
        "truncated": len(rows) > limit,
    }
