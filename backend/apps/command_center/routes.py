"""Command Center: one read that answers "what needs me right now?" for the signed-in role.

Each section reuses the query behind its own screen, so the numbers here are the numbers there. A
section the role may not see is left out (``null``); a section that fails reports its own error and
does not take the page down.
"""

import logging
import os
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from database import get_db
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.agents.action_center import row as case_row
from apps.agents.action_center import summary as work_summary
from apps.agents.action_center_filters import me_identifier
from apps.audit.error_routes import overview as error_overview
from apps.audit.routes import recent_events
from apps.cases.models import Case
from apps.cases.models import Exception as ExceptionRow
from apps.cash.routes import over_short_report
from apps.diagnostics.feeds import feed_status
from apps.diagnostics.health import system_health
from apps.exports.models import ExportBatch, ExportDestination
from apps.operations.batch_routes import status as batch_status
from apps.security import scope
from apps.security.authentication import require_role
from apps.store_days.models import AuditTotal, StoreDay
from apps.store_days.readiness import (
    CLEAN,
    COMPLETE,
    NOT_EXPORTED,
    export_states,
    holding_back,
    late_stores,
    readiness,
    unresolved_sequence_gap_counts,
)
from apps.store_days.routes import store_days_needing_attention
from apps.totals.over_short import TOTAL_NAME as OVER_SHORT_TOTAL
from apps.transactions.models import CanonicalTransaction
from apps.vouchers.routes import voucher_summary

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/command-center", tags=["Command Center"])

QUEUE_SIZE = 8
ERROR_TYPES = 5
ATTENTION_ROWS = 5
ERROR_WINDOW_DAYS = 30
# Settings: NIMBUS_KPI_WINDOW_DAYS is how far back the monitoring KPIs look.
KPI_WINDOW_DAYS = int(os.getenv("NIMBUS_KPI_WINDOW_DAYS", "30"))
# A store day is on time when it is closed within NIMBUS_CLOSE_TARGET_DAYS of its business date.
CLOSE_TARGET_DAYS = int(os.getenv("NIMBUS_CLOSE_TARGET_DAYS", "3"))

WORK_ROLES = ("admin", "analyst", "sales_audit_manager", "finance", "it", "auditor")
# Every Command Center role can open the Store Days workspace. Keep its audit-status summary
# available to the Sales Auditor and Internal Auditor roles as well as their managers.
STORE_DAY_ROLES = ("admin", "analyst", "sales_audit_manager", "finance", "it", "auditor")
ERROR_ROLES = ("admin", "analyst", "sales_audit_manager", "finance", "it", "auditor")
EXPORT_ROLES = ("admin", "finance", "it", "auditor")
HEALTH_ROLES = ("admin", "finance", "it", "auditor")
CASH_ROLES = ("admin", "analyst", "sales_audit_manager", "finance", "it", "auditor")
ACTIVITY_ROLES = ("admin", "analyst", "sales_audit_manager", "finance", "it", "auditor")
KPI_ROLES = ("admin", "analyst", "sales_audit_manager", "finance", "it", "auditor")
CASH_WINDOW_DAYS = 7
ACTIVITY_ROWS = 8
HOTSPOT_ROWS = 5
# Readiness is checked for at most this many open store days of one business date.
CLOSE_CHECK_LIMIT = 500


def close_blocker_counts():
    return (
        select(
            ExceptionRow.case_id.label("case_id"),
            func.count(ExceptionRow.id).label("close_blocking_errors"),
        )
        .where(
            ExceptionRow.close_impact == "Yes",
            ExceptionRow.status.not_in(("Overridden", "Closed", "Resolved")),
        )
        .group_by(ExceptionRow.case_id)
        .subquery()
    )


async def prioritized_cases(
    db: AsyncSession, filters: list, user: dict, now: datetime
) -> list[dict]:
    blockers = close_blocker_counts()
    statement = (
        select(Case, func.coalesce(blockers.c.close_blocking_errors, 0))
        .outerjoin(blockers, blockers.c.case_id == Case.id)
        .where(*filters)
        .order_by(
            func.coalesce(blockers.c.close_blocking_errors, 0).desc(),
            Case.sla_due_at.asc().nulls_last(),
            Case.total_exposure.desc(),
        )
        .limit(QUEUE_SIZE)
    )
    rows = (await db.execute(scope.restrict(statement, user, Case.store_id))).all()
    return [{**case_row(item, now), "close_blocking_errors": count} for item, count in rows]


async def queue(db: AsyncSession, user: dict) -> list[dict]:
    """The cases to look at first: breached or nearest SLA, skipping anything snoozed."""
    now = datetime.now(UTC)
    filters = [
        Case.status != "Closed",
        or_(Case.snoozed_until.is_(None), Case.snoozed_until <= now),
    ]
    return await prioritized_cases(db, filters, user, now)


async def mine(db: AsyncSession, user: dict) -> list[dict]:
    """Open cases assigned to the signed-in user, nearest SLA first."""
    now = datetime.now(UTC)
    filters = [Case.status != "Closed", func.lower(Case.assigned_to) == me_identifier(user)]
    return await prioritized_cases(db, filters, user, now)


async def hotspots(db: AsyncSession, user: dict) -> dict:
    """Where open cases are piling up: the stores and case types with the most, and how many are late."""
    now = datetime.now(UTC)
    late = func.sum(case((Case.sla_due_at < now, 1), else_=0))

    async def top(column) -> list[dict]:
        query = (
            select(column, func.count(), late)
            .where(Case.status != "Closed")
            .group_by(column)
            .order_by(func.count().desc(), column)
            .limit(HOTSPOT_ROWS)
        )
        rows = (await db.execute(scope.restrict(query, user, Case.store_id))).all()
        return [{"name": name, "open": n, "past_sla": int(p or 0)} for name, n, p in rows]

    return {"stores": await top(Case.store_id), "types": await top(Case.case_type)}


async def work(db: AsyncSession, user: dict) -> dict:
    return {
        "counts": await work_summary(db=db, user=user),
        "queue": await queue(db, user),
        "mine": await mine(db, user),
        "hotspots": await hotspots(db, user),
    }


async def store_days(db: AsyncSession, user: dict, business_date: str | None = None) -> dict:
    selected_date = business_date or (datetime.now(UTC).date() - timedelta(days=1)).isoformat()
    data = await store_days_needing_attention(
        business_date=selected_date,
        stuck_after_days=3,
        limit=200,
        db=db,
        user=user,
        late_page=1,
        late_page_size=ATTENTION_ROWS * 2,
    )
    gap_days = (
        (
            await db.execute(
                scope.restrict(
                    select(StoreDay).where(StoreDay.business_date == selected_date),
                    user,
                    StoreDay.store_id,
                ).order_by(StoreDay.store_id)
            )
        )
        .scalars()
        .all()
    )
    gap_counts = await unresolved_sequence_gap_counts(db, gap_days)
    gap_rows = sorted(
        (
            {"store_id": day.store_id, "missing": gap_counts.get((day.store_id, selected_date), 0)}
            for day in gap_days
            if gap_counts.get((day.store_id, selected_date), 0) > 0
        ),
        key=lambda row: (-row["missing"], row["store_id"]),
    )
    return {
        "business_date": data["business_date"],
        "expected_stores": data["expected_stores"],
        "late_total": data["late_total"],
        "late_stores": data["late_stores"][:ATTENTION_ROWS],
        "stuck_total": len(data["stuck_store_days"]),
        "stuck_truncated": data["stuck_truncated"],
        "stuck": [
            {
                "store_id": d["store_id"],
                "business_date": d["business_date"],
                "status": d["status"],
                "days_open": d["days_open"],
                "holding_back": d["holding_back"][:2],
            }
            for d in data["stuck_store_days"][:ATTENTION_ROWS]
        ],
        "missing_transactions": {
            "store_days": len(gap_rows),
            "numbers": sum(row["missing"] for row in gap_rows),
            "top": gap_rows[:ATTENTION_ROWS],
            "top_truncated": len(gap_rows) > ATTENTION_ROWS,
        },
    }


async def audit_errors(db: AsyncSession, user: dict) -> dict:
    data = await error_overview(db=db, user=user, days=ERROR_WINDOW_DAYS)
    top = [i for i in data["items"] if i["open_count"]][:ERROR_TYPES]
    return {
        "open_total": data["open_total"],
        "open_types": data["open_types"],
        "window_days": data["window_days"],
        "top": [
            {
                "code": i["code"],
                "label": i["label"],
                "open_count": i["open_count"],
                "open_by_currency": i["open_by_currency"],
            }
            for i in top
        ],
    }


async def exports(db: AsyncSession, _user: dict) -> dict:
    since = datetime.now(UTC) - timedelta(days=7)
    rows = (
        await db.execute(
            select(ExportBatch.status, func.count())
            .where(
                or_(
                    ExportBatch.status.in_(["Pending", "Failed"]),
                    and_(ExportBatch.status == "Sent", ExportBatch.created_at >= since),
                )
            )
            .group_by(ExportBatch.status)
        )
    ).all()
    counts = {status: n for status, n in rows}
    failed = (
        await db.execute(
            select(
                ExportBatch.id,
                ExportBatch.dataset,
                ExportDestination.name,
                ExportBatch.error,
                ExportBatch.created_at,
                ExportBatch.retries,
            )
            .join(ExportDestination, ExportDestination.id == ExportBatch.destination_id)
            .where(ExportBatch.status == "Failed")
            .order_by(ExportBatch.created_at.desc())
            .limit(ATTENTION_ROWS)
        )
    ).all()
    return {
        "failed": counts.get("Failed", 0),
        "pending": counts.get("Pending", 0),
        "awaiting_ack": counts.get("Sent", 0),
        "failed_batches": [
            {
                "id": batch_id,
                "dataset": dataset,
                "destination": destination,
                "error": error,
                "created_at": created_at,
                "retries": retries,
            }
            for batch_id, dataset, destination, error, created_at, retries in failed
        ],
    }


async def close(
    db: AsyncSession, user: dict, business_date: str | None = None
) -> dict:
    """How far a business date is from closed: closed, ready, blocked and why, and export."""
    business_date = business_date or (datetime.now(UTC).date() - timedelta(days=1)).isoformat()
    expected, _late = await late_stores(db, user, business_date)
    days = (
        (
            await db.execute(
                scope.restrict(
                    select(StoreDay).where(StoreDay.business_date == business_date),
                    user,
                    StoreDay.store_id,
                ).order_by(StoreDay.store_id)
            )
        )
        .scalars()
        .all()
    )
    closed = [d for d in days if d.status == "Closed"]
    open_days = [d for d in days if d.status != "Closed"]
    checked = open_days[:CLOSE_CHECK_LIMIT]
    views = await readiness(db, checked) if checked else {}
    ready = [d for d in checked if views[d.id]["ready_to_close"]]
    blocked = [d for d in checked if not views[d.id]["ready_to_close"]]

    reasons: Counter[str] = Counter()
    for d in blocked:
        view = views[d.id]
        if view["ingest"]["state"] != COMPLETE:
            reasons[f"Data {view['ingest']['state'].lower()}"] += 1
        if view["audit"]["state"] != CLEAN:
            reasons[view["audit"]["state"]] += 1

    exported = await export_states(db, [(d.store_id, d.business_date) for d in closed])
    export_counts: Counter[str] = Counter(
        exported.get((d.store_id, d.business_date), {}).get("state", NOT_EXPORTED) for d in closed
    )
    return {
        "business_date": business_date,
        "expected_stores": expected,
        "opened": len(days),
        "closed": len(closed),
        "ready": len(ready),
        "blocked": len(blocked),
        "unchecked": len(open_days) - len(checked),
        "blocked_reasons": [{"reason": r, "store_days": n} for r, n in reasons.most_common()],
        "blocked_examples": [
            {"store_id": d.store_id, "reasons": holding_back(views[d.id]) or [f"Status {d.status}"]}
            for d in blocked[:ATTENTION_ROWS]
        ],
        "export": dict(export_counts),
    }


async def balancing(db: AsyncSession, user: dict, business_date: str | None = None) -> dict:
    """Current store-level ReSA Over/Short totals, kept separate by currency."""
    business_date = business_date or (datetime.now(UTC).date() - timedelta(days=1)).isoformat()
    filters = [
        StoreDay.business_date == business_date,
        StoreDay.audit_version >= 1,
        AuditTotal.audit_version == StoreDay.audit_version,
        AuditTotal.total_name == OVER_SHORT_TOTAL,
        AuditTotal.level == "Store",
        AuditTotal.variance_amount.is_not(None),
    ]
    stale = StoreDay.totals_stale.is_(True)
    current = StoreDay.totals_stale.is_(False)
    summary = (
        select(
            AuditTotal.currency,
            func.count(func.distinct(case((current, StoreDay.id), else_=None))).label(
                "store_days"
            ),
            func.count(func.distinct(case((stale, StoreDay.id), else_=None))).label(
                "stale_store_days"
            ),
            func.count(
                func.distinct(
                    case(
                        (and_(current, AuditTotal.variance_amount != 0), StoreDay.id),
                        else_=None,
                    )
                )
            ).label("out_of_balance"),
            func.sum(
                case(
                    (and_(current, AuditTotal.variance_amount > 0), AuditTotal.variance_amount),
                    else_=0,
                )
            ).label("short"),
            func.sum(
                case(
                    (and_(current, AuditTotal.variance_amount < 0), -AuditTotal.variance_amount),
                    else_=0,
                )
            ).label("over"),
        )
        .join(StoreDay, StoreDay.id == AuditTotal.store_day_id)
        .where(*filters)
        .group_by(AuditTotal.currency)
    )
    summary_rows = (
        await db.execute(
            scope.restrict(summary, user, StoreDay.store_id).order_by(AuditTotal.currency)
        )
    ).all()
    detail = (
        select(
            StoreDay.store_id,
            StoreDay.status,
            AuditTotal.currency,
            AuditTotal.variance_amount,
            func.row_number()
            .over(
                partition_by=AuditTotal.currency,
                order_by=(func.abs(AuditTotal.variance_amount).desc(), StoreDay.store_id),
            )
            .label("rank"),
        )
        .join(StoreDay, StoreDay.id == AuditTotal.store_day_id)
        .where(*filters, current, AuditTotal.variance_amount != 0)
    )
    ranked = scope.restrict(detail, user, StoreDay.store_id).subquery()
    detail_rows = (
        await db.execute(
            select(ranked)
            .where(ranked.c.rank <= ATTENTION_ROWS + 1)
            .order_by(ranked.c.currency, ranked.c.rank)
        )
    ).all()
    top_rows = [row for row in detail_rows if row.rank <= ATTENTION_ROWS]
    return {
        "business_date": business_date,
        "by_currency": [
            {
                "currency": row.currency,
                "store_days": row.store_days,
                "stale_store_days": row.stale_store_days,
                "out_of_balance": row.out_of_balance,
                "short": str(row.short or 0),
                "over": str(row.over or 0),
            }
            for row in summary_rows
        ],
        "top": [
            {
                "store_id": row.store_id,
                "status": row.status,
                "currency": row.currency,
                "variance_amount": str(row.variance_amount),
            }
            for row in top_rows
        ],
        "top_truncated": any(row.rank > ATTENTION_ROWS for row in detail_rows),
    }


async def health(db: AsyncSession, _user: dict) -> dict:
    data = await system_health(db)
    attention = [c for c in data.get("checks", []) if c.get("status") != "ok"]
    return {
        "status": data.get("status"),
        "attention": [
            {"name": c.get("name"), "status": c.get("status"), "detail": c.get("detail")}
            for c in attention[:ATTENTION_ROWS]
        ],
        "attention_total": len(attention),
    }


async def feeds(db: AsyncSession, _user: dict) -> dict:
    data = await feed_status(db)
    return {
        "stale_after_hours": data["stale_after_hours"],
        "feeds": [
            {
                "source_system": f["source_system"],
                "state": f["state"],
                "last_accepted_at": f["last_accepted_at"],
                "received_24h": f["received_24h"],
                "quarantined_total": f["quarantined_total"],
            }
            for f in data["feeds"]
        ],
        "quarantine_reasons": data["quarantine_reasons_7d"][:ATTENTION_ROWS],
    }


async def cash(db: AsyncSession, user: dict) -> dict:
    today = datetime.now(UTC).date()
    start = (today - timedelta(days=CASH_WINDOW_DAYS - 1)).isoformat()
    report = await over_short_report(
        group_by="date",
        start=start,
        end=today.isoformat(),
        store_id=None,
        db=db,
        user=user,
    )
    vouchers = await voucher_summary(db=db, user=user)

    def by_currency(group: dict) -> list[dict]:
        return [
            {"currency": cur, "vouchers": v["vouchers"], "amount": str(v["amount"])}
            for cur, v in sorted(group.items(), key=lambda kv: str(kv[0]))
        ]

    return {
        "window_days": CASH_WINDOW_DAYS,
        "declarations": report["totals"]["declarations"],
        "out_of_balance": sum(r["out_of_balance"] or 0 for r in report["rows"]),
        "short": str(report["totals"]["short"]),
        "over": str(report["totals"]["over"]),
        "vouchers_outstanding": by_currency(vouchers["outstanding"]),
        "vouchers_expired": by_currency(vouchers["expired"]),
    }


async def activity(db: AsyncSession, user: dict) -> dict:
    page = await recent_events(limit=ACTIVITY_ROWS, before_seq=None, db=db, _user=user)
    return {
        "events": [
            {
                "seq": e["seq"],
                "created_at": e["created_at"],
                "actor": e["actor"],
                "event_type": e["event_type"],
                "description": e["description"],
                "case_id": e["case_id"],
            }
            for e in page["items"]
        ]
    }


def percent(part: int, whole: int) -> float | None:
    return round(100 * part / whole, 1) if whole else None


async def kpis(db: AsyncSession, user: dict) -> dict:
    """Ongoing monitoring: transaction accuracy, exception rate and timeliness over the last month.

    Accuracy is the share of transactions that were not flagged "Exception" by validation. The
    exception rate counts every exception raised, per 1,000 transactions. Timeliness is the share
    of store days now due that were closed within CLOSE_TARGET_DAYS of their business date.
    """
    today = datetime.now(UTC).date()
    since = (today - timedelta(days=KPI_WINDOW_DAYS)).isoformat()
    transactions = (
        await db.execute(
            scope.restrict(
                select(func.count(CanonicalTransaction.id)).where(
                    CanonicalTransaction.business_date >= since
                ),
                user,
                CanonicalTransaction.store_id,
            )
        )
    ).scalar_one()
    flagged = (
        await db.execute(
            scope.restrict(
                select(func.count(CanonicalTransaction.id)).where(
                    CanonicalTransaction.business_date >= since,
                    CanonicalTransaction.reconciliation_status == "Exception",
                ),
                user,
                CanonicalTransaction.store_id,
            )
        )
    ).scalar_one()
    exceptions = (
        await db.execute(
            scope.restrict(
                select(func.count(ExceptionRow.id))
                .join(Case, Case.id == ExceptionRow.case_id)
                .where(Case.business_date >= since),
                user,
                Case.store_id,
            )
        )
    ).scalar_one()
    days = (
        await db.execute(
            scope.restrict(
                select(StoreDay.business_date, StoreDay.status, StoreDay.closed_at).where(
                    StoreDay.business_date >= since,
                    StoreDay.business_date
                    <= (today - timedelta(days=CLOSE_TARGET_DAYS)).isoformat(),
                ),
                user,
                StoreDay.store_id,
            )
        )
    ).all()
    on_time = 0
    for business_date, status, closed_at in days:
        if status == "Closed" and closed_at is not None:
            due = date.fromisoformat(business_date) + timedelta(days=CLOSE_TARGET_DAYS)
            on_time += closed_at.date() <= due
    return {
        "window_days": KPI_WINDOW_DAYS,
        "transactions": transactions,
        "exceptions": exceptions,
        "exception_rate": round(1000 * exceptions / transactions, 1) if transactions else None,
        "flagged_transactions": flagged,
        "accuracy": percent(transactions - flagged, transactions),
        "close_target_days": CLOSE_TARGET_DAYS,
        "due_store_days": len(days),
        "closed_on_time": on_time,
        "timeliness": percent(on_time, len(days)),
    }


async def section(db: AsyncSession, user: dict, roles: tuple[str, ...], build, system_wide=False):
    if user["role"] not in roles or (system_wide and scope.scoped(user)):
        return None
    try:
        return await build(db, user)
    except Exception:  # one failing section must not blank the page
        logger.exception("Command Center section %s failed", build.__name__)
        await db.rollback()
        return {"error": "This section could not be loaded"}


@router.get("")
async def command_center(
    business_date: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}-\d{2}$")] = None,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role(*WORK_ROLES)),
):
    if business_date:
        try:
            if date.fromisoformat(business_date).isoformat() != business_date:
                raise ValueError
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="business_date must be YYYY-MM-DD") from exc
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "role": user["role"],
        "freshness": await section(db, user, WORK_ROLES, lambda d, _u: batch_status(d)),
        "work": await section(db, user, WORK_ROLES, work),
        "store_days": await section(
            db, user, STORE_DAY_ROLES, lambda d, u: store_days(d, u, business_date)
        ),
        "close": await section(
            db, user, STORE_DAY_ROLES, lambda d, u: close(d, u, business_date)
        ),
        "balancing": await section(
            db, user, STORE_DAY_ROLES, lambda d, u: balancing(d, u, business_date)
        ),
        "audit_errors": await section(db, user, ERROR_ROLES, audit_errors),
        "exports": await section(db, user, EXPORT_ROLES, exports),
        "health": await section(db, user, HEALTH_ROLES, health, system_wide=True),
        "feeds": await section(db, user, HEALTH_ROLES, feeds, system_wide=True),
        "cash": await section(db, user, CASH_ROLES, cash),
        "activity": await section(db, user, ACTIVITY_ROLES, activity),
        "kpis": await section(db, user, KPI_ROLES, kpis),
    }
