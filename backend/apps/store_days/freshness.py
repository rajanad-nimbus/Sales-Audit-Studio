"""Invalidate store-day totals and closure evidence after source changes."""
from collections import defaultdict

from integrations.ontology.nimbus import audit
from sqlalchemy import case, select, text, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.store_days.models import StoreDay, StoreDayClosureRequest

CONFIGURATION_LOCK_KEY = 665302210171


async def lock_configuration(db: AsyncSession, *, exclusive: bool = False) -> None:
    """Serialize PostgreSQL configuration writes with opening and retotaling days."""
    if db.get_bind().dialect.name == "postgresql":
        function = "pg_advisory_xact_lock" if exclusive else "pg_advisory_xact_lock_shared"
        await db.execute(text(f"SELECT {function}(:key)"), {"key": CONFIGURATION_LOCK_KEY})


async def lock_store_day_identity(db: AsyncSession, store_id: str, business_date: str) -> None:
    """Serialize operations for a store day, including before its row exists."""
    if db.get_bind().dialect.name != "postgresql":
        return
    key = f"store-day:{len(store_id)}:{store_id}:{business_date}"
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key})


async def lock_store_days(
    db: AsyncSession,
    identities: set[tuple[str, str]],
) -> dict[tuple[str, str], StoreDay]:
    """Lock a deterministic set of store days with bounded query batches."""
    ordered_identities = sorted(identities)
    if not ordered_identities:
        return {}

    if db.get_bind().dialect.name == "postgresql":
        lock_keys = [
            f"store-day:{len(store_id)}:{store_id}:{business_date}"
            for store_id, business_date in ordered_identities
        ]
        await db.execute(text(
            "SELECT pg_advisory_xact_lock(hashtextextended(lock_key, 0)) "
            "FROM unnest(CAST(:lock_keys AS text[])) AS lock_rows(lock_key) "
            "ORDER BY lock_key"
        ), {"lock_keys": lock_keys})

    days: dict[tuple[str, str], StoreDay] = {}
    for offset in range(0, len(ordered_identities), 250):
        identity_batch = ordered_identities[offset:offset + 250]
        rows = (await db.execute(
            select(StoreDay).where(
                tuple_(StoreDay.store_id, StoreDay.business_date).in_(identity_batch)
            ).order_by(StoreDay.store_id, StoreDay.business_date)
            .with_for_update().execution_options(populate_existing=True)
        )).scalars()
        days.update({(day.store_id, day.business_date): day for day in rows})
    return days


async def lock_store_day(db: AsyncSession, store_id: str, business_date: str) -> StoreDay | None:
    return (await lock_store_days(db, {(store_id, business_date)})).get((store_id, business_date))


async def invalidate_totals(db: AsyncSession, day: StoreDay, actor: str) -> None:
    changed = day.audit_version > 0 and not day.totals_stale
    if day.audit_version > 0:
        day.totals_stale = True
    pending = (await db.execute(select(StoreDayClosureRequest).where(
        StoreDayClosureRequest.store_day_id == day.id,
        StoreDayClosureRequest.status == "Pending"))).scalars().all()
    for request in pending:
        request.status = "Superseded"
        request.review_reason = "Source data changed; recalculate totals and submit new closure evidence"
    if changed or pending:
        audit(db, "STORE_DAY_SOURCE_CHANGED", None, "StoreDay", day.id,
              "Source data changed; refresh store-day totals and closure evidence", actor=actor)


async def invalidate_totals_many(db: AsyncSession, days: list[StoreDay], actor: str) -> None:
    """Invalidate totals and pending closure requests for many changed days in bounded queries."""
    ordered_days = sorted({day.id: day for day in days}.values(), key=lambda day: (day.store_id, day.business_date))
    if not ordered_days:
        return

    pending_by_day: dict[str, list[StoreDayClosureRequest]] = defaultdict(list)
    day_ids = [day.id for day in ordered_days]
    for offset in range(0, len(day_ids), 500):
        pending_requests = (await db.execute(select(StoreDayClosureRequest).where(
            StoreDayClosureRequest.store_day_id.in_(day_ids[offset:offset + 500]),
            StoreDayClosureRequest.status == "Pending",
        ))).scalars()
        for request in pending_requests:
            pending_by_day[request.store_day_id].append(request)

    for day in ordered_days:
        changed = day.audit_version > 0 and not day.totals_stale
        if day.audit_version > 0:
            day.totals_stale = True
        pending = pending_by_day[day.id]
        for request in pending:
            request.status = "Superseded"
            request.review_reason = "Source data changed; recalculate totals and submit new closure evidence"
        if changed or pending:
            audit(db, "STORE_DAY_SOURCE_CHANGED", None, "StoreDay", day.id,
                  "Source data changed; refresh store-day totals and closure evidence", actor=actor)


async def invalidate_configuration(
    db: AsyncSession, *, actor: str, reason: str, start_date: str | None = None,
    end_date: str | None = None, stores: set[str] | None = None,
) -> None:
    """Invalidate affected calculated days in the same transaction as a configuration change."""
    if stores is not None and not stores:
        return
    await lock_configuration(db, exclusive=True)
    await invalidate_configuration_locked(
        db, actor=actor, reason=reason, start_date=start_date, end_date=end_date, stores=stores
    )


async def invalidate_configuration_locked(
    db: AsyncSession, *, actor: str, reason: str, start_date: str | None = None,
    end_date: str | None = None, stores: set[str] | None = None,
) -> None:
    """Invalidate days when the caller already owns the exclusive configuration lock."""
    if stores is not None and not stores:
        return
    conditions = []
    if start_date is not None:
        conditions.append(StoreDay.business_date >= start_date)
    if end_date is not None:
        conditions.append(StoreDay.business_date <= end_date)
    if stores is not None:
        conditions.append(StoreDay.store_id.in_(stores))
    # Lock even days at version zero: a concurrent first retotal may be in progress.
    result = await db.execute(update(StoreDay).where(*conditions)
        .values(totals_stale=case((StoreDay.audit_version > 0, True), else_=StoreDay.totals_stale))
        .execution_options(synchronize_session=False))
    affected_ids = select(StoreDay.id).where(*conditions)
    requests = await db.execute(update(StoreDayClosureRequest).where(
        StoreDayClosureRequest.store_day_id.in_(affected_ids), StoreDayClosureRequest.status == "Pending"
    ).values(status="Superseded", review_reason="Configuration changed; recalculate totals and submit new closure evidence")
        .execution_options(synchronize_session=False))
    if result.rowcount or requests.rowcount:
        audit(db, "STORE_DAY_CONFIGURATION_CHANGED", None, "Configuration", "totals",
              f"{reason}: {result.rowcount} store day(s) checked for stale totals", actor=actor)
