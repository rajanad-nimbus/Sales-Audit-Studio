"""Store and audit register events: till and drawer activity with no sale behind it."""

import uuid
from collections import Counter
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select

from apps.ingest.models import RegisterEvent
from apps.ingest.pos_formats import standard
from apps.transactions.models import CanonicalTransaction

CENT = Decimal("0.01")
NO_SALE_LIMIT = 5  # no-sales per register per day before the day is flagged
BATCH = 400


def event_amount(tx: dict) -> Decimal | None:
    """Cash moved by a paid-in/out, pull or loan (its tenders), or the value a TOTAL record carries."""
    if tx["type"] in standard.CASH_MOVEMENT_TYPES and tx["tenders"]:
        moved = sum((abs(Decimal(t["amount"])) for t in tx["tenders"]), Decimal("0"))
    elif tx.get("value"):
        moved = abs(Decimal(tx["value"]))
    else:
        return None
    return moved.quantize(CENT, rounding=ROUND_HALF_UP)


async def ingest_register_events(db, log: standard.StandardLog, currency: str = "USD") -> dict:
    """Store a log's register events; one already stored (same source record id) is skipped."""
    events = standard.register_events(log)
    known: set[str] = set()
    for start in range(0, len(events), BATCH):
        ids = [e["source_record_id"] for e in events[start : start + BATCH]]
        known |= set(
            (
                await db.execute(
                    select(RegisterEvent.source_record_id).where(
                        RegisterEvent.source_record_id.in_(ids)
                    )
                )
            )
            .scalars()
            .all()
        )
    created = 0
    for tx in events:
        if tx["source_record_id"] in known:
            continue
        known.add(tx["source_record_id"])
        db.add(
            RegisterEvent(
                id=str(uuid.uuid4()),
                source_record_id=tx["source_record_id"],
                source_format=log.source_format,
                store_id=tx["store_id"],
                business_date=tx["business_date"],
                register_id=tx["register_id"] or None,
                cashier_id=tx["cashier_id"] or None,
                event_time=datetime.fromisoformat(tx["event_time"]),
                event_type=tx["type"],
                sub_type=tx["sub_type"] or None,
                transaction_number=tx["transaction_number"],
                original_transaction_number=tx["original_transaction_number"],
                original_register_id=tx["original_register_id"],
                reason_code=tx["reason_code"],
                amount=event_amount(tx),
                currency=currency,
            )
        )
        created += 1
    await db.flush()
    return {
        "received": len(events),
        "created": created,
        "duplicates_skipped": len(events) - created,
    }


async def original_is_on_file(db, event: RegisterEvent) -> bool:
    """Is the transaction a post-void cancels stored, as a sale or return or as another register event?"""
    register = event.original_register_id or event.register_id
    number = event.original_transaction_number
    sale = await db.execute(
        select(func.count())
        .select_from(CanonicalTransaction)
        .where(
            CanonicalTransaction.store_id == event.store_id,
            CanonicalTransaction.register_id == register,
            CanonicalTransaction.payment_reference.like(f"%-{register}-{number}"),
        )
    )
    if sale.scalar_one():
        return True
    other = await db.execute(
        select(func.count())
        .select_from(RegisterEvent)
        .where(
            RegisterEvent.store_id == event.store_id,
            RegisterEvent.register_id == register,
            RegisterEvent.transaction_number == number,
            RegisterEvent.id != event.id,
        )
    )
    return bool(other.scalar_one())


async def run_register_event_rules(
    db, add, business_date: str | None = None, store_ids: set[str] | None = None
):
    """Audit events not yet audited: post-void with no original, paid-in/out with no reason, excess no-sales."""
    query = select(RegisterEvent).where(RegisterEvent.audited.is_(False))
    if business_date:
        query = query.where(RegisterEvent.business_date == business_date)
    if store_ids is not None:
        query = query.where(RegisterEvent.store_id.in_(store_ids))
    events = (
        (await db.execute(query.order_by(RegisterEvent.event_time, RegisterEvent.id)))
        .scalars()
        .all()
    )

    no_sales = Counter(
        (e.store_id, e.business_date, e.register_id)
        for e in events
        if e.event_type == standard.NO_SALE
    )
    flagged_days: set[tuple] = set()
    for event in events:
        code = None
        if event.event_type == standard.POST_VOID and event.original_transaction_number:
            if not await original_is_on_file(db, event):
                code = "VOID_NO_ORIG"
        elif event.event_type in (standard.PAID_IN, standard.PAID_OUT) and not event.reason_code:
            code = "PAIDOUT_REASON_REQ"
        elif event.event_type == standard.POST_VOID:
            code = "VOID_NO_ORIG"
        if code:
            event.audit_code = code
            add(
                code,
                event.store_id,
                event.business_date,
                event.amount or Decimal("0"),
                event.currency,
            )
        key = (event.store_id, event.business_date, event.register_id)
        if (
            event.event_type == standard.NO_SALE
            and no_sales[key] > NO_SALE_LIMIT
            and key not in flagged_days
        ):
            flagged_days.add(key)
            event.audit_code = "NOSALE_EXCESS"
            add("NOSALE_EXCESS", event.store_id, event.business_date, Decimal("0"), event.currency)
        event.audited = True
    await db.flush()
