"""Missing transaction numbers: a gap in a register's numbering means a transaction never arrived.

Registers number their transactions one after another through the business day. Sales and
returns, and register events such as voids and no-sales, all draw from that one sequence, so
the numbers seen across both should have no holes between the first and the last.
"""

from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from apps.cases.models import Case
from apps.ingest.common import LINEAGE
from apps.ingest.models import RegisterEvent
from apps.transactions.models import CanonicalTransaction

CODE = "MISSING_TRAN"
MAX_GAP_SPAN = 100_000  # a larger hole is a numbering reset, not missing transactions


def missing_numbers(numbers: set[int]) -> list[int]:
    """Numbers absent between the lowest and highest seen, when the span is plausible."""
    if len(numbers) < 2:
        return []
    low, high = min(numbers), max(numbers)
    if high - low > MAX_GAP_SPAN:
        return []
    return [n for n in range(low + 1, high) if n not in numbers]


def parse(number: str | None) -> int | None:
    """Real transaction numbers only; "0" marks records that carry none (till totals)."""
    if number is None or not number.isdigit():
        return None
    value = int(number)
    return value or None


async def find_gaps(
    db: AsyncSession,
    business_date: str | None = None,
    store_ids: set[str] | None = None,
    store_id: str | None = None,
) -> dict[tuple[str, str], dict[str, list[int]]]:
    """Missing numbers by store day, then register."""
    sales = select(
        CanonicalTransaction.store_id.label("store_id"),
        CanonicalTransaction.business_date.label("business_date"),
        CanonicalTransaction.register_id.label("register_id"),
        CanonicalTransaction.transaction_number.label("number"),
    ).where(
        CanonicalTransaction.source_lineage == LINEAGE["POS"],
        CanonicalTransaction.transaction_number.is_not(None),
        CanonicalTransaction.register_id.is_not(None),
    )
    events = select(
        RegisterEvent.store_id,
        RegisterEvent.business_date,
        RegisterEvent.register_id,
        RegisterEvent.transaction_number,
    ).where(RegisterEvent.register_id.is_not(None))
    if business_date:
        sales = sales.where(CanonicalTransaction.business_date == business_date)
        events = events.where(RegisterEvent.business_date == business_date)
    if store_ids is not None:
        sales = sales.where(CanonicalTransaction.store_id.in_(store_ids))
        events = events.where(RegisterEvent.store_id.in_(store_ids))
    if store_id:
        sales = sales.where(CanonicalTransaction.store_id == store_id)
        events = events.where(RegisterEvent.store_id == store_id)

    seen: dict[tuple[str, str, str], set[int]] = defaultdict(set)
    for store, day, register, number in (await db.execute(union_all(sales, events))).all():
        value = parse(number)
        if value is not None:
            seen[(store, day, register)].add(value)

    found: dict[tuple[str, str], dict[str, list[int]]] = defaultdict(dict)
    for (store, day, register), numbers in sorted(seen.items()):
        gaps = missing_numbers(numbers)
        if gaps:
            found[(store, day)][register] = gaps
    return dict(found)


async def run_sequence_rules(
    db: AsyncSession,
    add,
    business_date: str | None = None,
    store_ids: set[str] | None = None,
) -> int:
    """Raise one MISSING_TRAN finding per store day with gaps. Returns how many it raised."""
    by_day = await find_gaps(db, business_date, store_ids)
    if not by_day:
        return 0

    # A day already holding an open gap case is not raised again on every run.
    open_cases = {
        (store, day)
        for store, day in (
            await db.execute(
                select(Case.store_id, Case.business_date).where(
                    Case.case_type == "Missing Transaction Number",
                    Case.status != "Closed",
                    Case.store_id.in_({store for store, _ in by_day}),
                )
            )
        ).all()
    }
    raised = 0
    for store, day in by_day:
        if (store, day) in open_cases:
            continue
        currency = (
            await db.execute(
                select(CanonicalTransaction.currency)
                .where(
                    CanonicalTransaction.store_id == store,
                    CanonicalTransaction.business_date == day,
                )
                .limit(1)
            )
        ).scalar_one_or_none() or "USD"
        add(CODE, store, day, Decimal("0"), currency)
        raised += 1
    return raised
