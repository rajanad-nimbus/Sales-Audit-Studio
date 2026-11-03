"""Repeating audit errors: the same error code coming back at one store or one register.

Errors are counted by the business days they occurred on, so a register that raised one
error a hundred times in a day counts once, and a register that raises it every day stands
out. Registers are found through the transactions a case was raised for (its lineage) and
through register events, so nothing new is stored.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from apps.audit.models import AuditEvent
from apps.cases.lineage import case_transaction_ids_table
from apps.cases.models import Case
from apps.cases.models import Exception as ExceptionRow
from apps.ingest.models import RegisterEvent
from apps.security import scope
from apps.transactions.models import CanonicalTransaction

MAX_ROWS = 200
RISING, FALLING, STEADY = "Rising", "Falling", "Steady"


@dataclass(slots=True)
class Occurrences:
    days: set[str] = field(default_factory=set)
    sources: set[str] = field(default_factory=set)


def trend(days: set[str], since: date, until: date) -> str:
    """Compare the later half of the window with the earlier half."""
    middle = (since + (until - since) / 2).isoformat()
    earlier = sum(1 for day in days if day < middle)
    later = len(days) - earlier
    return RISING if later > earlier else FALLING if later < earlier else STEADY


async def store_rows(db, user, since: str, until: str):
    query = (
        select(Case.store_id, ExceptionRow.exception_type, Case.business_date)
        .join(ExceptionRow, ExceptionRow.case_id == Case.id)
        .where(Case.business_date >= since, Case.business_date <= until)
        .distinct()
    )
    return (await db.execute(scope.restrict(query, user, Case.store_id))).all()


async def register_rows_from_lineage(db, user, since: str, until: str):
    created = aliased(AuditEvent)
    transaction_ids = case_transaction_ids_table(db, created)
    query = (
        select(
            Case.store_id,
            CanonicalTransaction.register_id,
            ExceptionRow.exception_type,
            Case.business_date,
        )
        .select_from(created)
        .join(transaction_ids, true())
        .join(CanonicalTransaction, CanonicalTransaction.id == transaction_ids.c.value)
        .join(Case, Case.id == created.case_id)
        .join(ExceptionRow, ExceptionRow.case_id == Case.id)
        .where(
            created.event_type == "CASE_CREATED",
            CanonicalTransaction.register_id.is_not(None),
            CanonicalTransaction.store_id == Case.store_id,
            Case.business_date >= since,
            Case.business_date <= until,
        )
        .distinct()
    )
    return (await db.execute(scope.restrict(query, user, Case.store_id))).all()


async def register_rows_from_events(db, user, since: str, until: str):
    query = (
        select(
            RegisterEvent.store_id,
            RegisterEvent.register_id,
            RegisterEvent.audit_code,
            RegisterEvent.business_date,
        )
        .where(
            RegisterEvent.audit_code.is_not(None),
            RegisterEvent.register_id.is_not(None),
            RegisterEvent.business_date >= since,
            RegisterEvent.business_date <= until,
        )
        .distinct()
    )
    return (await db.execute(scope.restrict(query, user, RegisterEvent.store_id))).all()


def summarise(groups: dict[tuple, Occurrences], since: date, until: date, min_days: int):
    rows = []
    for key, found in groups.items():
        days = sorted(found.days)
        rows.append(
            {
                "key": key,
                "days": len(days),
                "first_seen": days[0],
                "last_seen": days[-1],
                "trend": trend(found.days, since, until),
                "repeating": len(days) >= min_days,
                "sources": sorted(found.sources),
            }
        )
    rows.sort(key=lambda row: (not row["repeating"], -row["days"], row["key"]))
    return rows[:MAX_ROWS], len(rows) > MAX_ROWS


async def error_patterns(
    db: AsyncSession, user: dict, until: date, window_days: int, min_days: int, label
) -> dict:
    since = until - timedelta(days=window_days - 1)
    bounds = (since.isoformat(), until.isoformat())

    by_register: dict[tuple, Occurrences] = defaultdict(Occurrences)
    for store, register, code, day in await register_rows_from_lineage(db, user, *bounds):
        by_register[(store, register, code)].days.add(day)
        by_register[(store, register, code)].sources.add("Transactions")
    for store, register, code, day in await register_rows_from_events(db, user, *bounds):
        by_register[(store, register, code)].days.add(day)
        by_register[(store, register, code)].sources.add("Register events")

    by_store: dict[tuple, Occurrences] = defaultdict(Occurrences)
    for store, code, day in await store_rows(db, user, *bounds):
        by_store[(store, code)].days.add(day)
        by_store[(store, code)].sources.add("Cases")
    # Register-event errors raise no case, so add them to their store as well.
    for (store, _register, code), found in by_register.items():
        if "Register events" in found.sources:
            by_store[(store, code)].days.update(found.days)
            by_store[(store, code)].sources.add("Register events")

    registers, registers_truncated = summarise(by_register, since, until, min_days)
    stores, stores_truncated = summarise(by_store, since, until, min_days)

    store_days: dict[str, set[str]] = defaultdict(set)
    store_codes: dict[str, set[str]] = defaultdict(set)
    for (store, code), found in by_store.items():
        store_days[store].update(found.days)
        store_codes[store].add(code)
    store_summary = sorted(
        (
            {
                "store_id": store,
                "error_days": len(days),
                "error_codes": len(store_codes[store]),
                "repeating_codes": sum(
                    1
                    for (row_store, _code), found in by_store.items()
                    if row_store == store and len(found.days) >= min_days
                ),
                "trend": trend(days, since, until),
            }
            for store, days in store_days.items()
        ),
        key=lambda row: (-row["repeating_codes"], -row["error_days"], row["store_id"]),
    )

    def register_view(row):
        store, register, code = row.pop("key")
        return {"store_id": store, "register_id": register, "code": code, **label(code), **row}

    def store_view(row):
        store, code = row.pop("key")
        return {"store_id": store, "code": code, **label(code), **row}

    return {
        "since": bounds[0],
        "until": bounds[1],
        "window_days": window_days,
        "min_days": min_days,
        "registers": [register_view(row) for row in registers],
        "registers_truncated": registers_truncated,
        "stores": [store_view(row) for row in stores],
        "stores_truncated": stores_truncated,
        "store_summary": store_summary[:MAX_ROWS],
    }
