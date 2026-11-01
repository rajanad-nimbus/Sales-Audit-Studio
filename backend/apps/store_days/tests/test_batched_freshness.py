import asyncio

from apps.store_days.freshness import invalidate_totals_many, lock_store_days
from models import AuditEvent, Base, StoreDay, StoreDayClosureRequest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


def test_lock_store_days_uses_bounded_selects_for_many_identities():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            identities = {(f"S{index:03}", "2026-10-01") for index in range(501)}
            async with sessions() as db:
                db.add_all(
                    StoreDay(
                        id=f"day-{index:03}",
                        store_id=store_id,
                        business_date=business_date,
                    )
                    for index, (store_id, business_date) in enumerate(sorted(identities))
                )
                await db.commit()

                select_count = 0

                def count_store_day_selects(
                    _connection, _cursor, statement, _parameters, _context, _executemany
                ):
                    nonlocal select_count
                    normalized = statement.upper()
                    if normalized.lstrip().startswith("SELECT") and "FROM STORE_DAYS" in normalized:
                        select_count += 1

                event.listen(engine.sync_engine, "before_cursor_execute", count_store_day_selects)
                try:
                    locked = await lock_store_days(db, identities)
                finally:
                    event.remove(
                        engine.sync_engine, "before_cursor_execute", count_store_day_selects
                    )

                assert len(locked) == len(identities)
                assert select_count == 3
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_invalidate_totals_many_preserves_freshness_and_closure_semantics():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with sessions() as db:
                changed_day = StoreDay(
                    id="changed",
                    store_id="S1",
                    business_date="2026-10-01",
                    audit_version=2,
                )
                closure_only_day = StoreDay(
                    id="closure-only",
                    store_id="S2",
                    business_date="2026-10-01",
                )
                unchanged_day = StoreDay(
                    id="unchanged",
                    store_id="S3",
                    business_date="2026-10-01",
                )
                db.add_all([changed_day, closure_only_day, unchanged_day])
                db.add_all(
                    [
                        StoreDayClosureRequest(
                            id=f"request-{day.id}",
                            store_day_id=day.id,
                            requested_by="reviewer",
                            evidence_basis="verified",
                            status="Pending",
                        )
                        for day in (changed_day, closure_only_day)
                    ]
                )
                await db.flush()

                await invalidate_totals_many(
                    db,
                    [unchanged_day, closure_only_day, changed_day],
                    "ingest",
                )
                await db.commit()

                assert changed_day.totals_stale
                assert not closure_only_day.totals_stale
                assert not unchanged_day.totals_stale
                requests = (await db.execute(select(StoreDayClosureRequest))).scalars().all()
                assert {request.status for request in requests} == {"Superseded"}
                events = (
                    (
                        await db.execute(
                            select(AuditEvent).where(
                                AuditEvent.event_type == "STORE_DAY_SOURCE_CHANGED",
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                assert {event.object_id for event in events} == {"changed", "closure-only"}
        finally:
            await engine.dispose()

    asyncio.run(run())
