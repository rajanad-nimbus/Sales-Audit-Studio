"""The operating plan reports real state: data readiness, and which store days can be balanced."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from models import (
    AuditTotal,
    Base,
    BatchRun,
    CanonicalTransaction,
    FoundationReference,
    SourceRecord,
    StoreDay,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.command_center import plan as plan_module
from apps.command_center.plan import CRITICAL, OK, WARNING, data_status, operating_plan

NOW = datetime(2026, 10, 10, 12, tzinfo=UTC)
YESTERDAY = "2026-10-09"
FINANCE = {"user": "finance", "role": "finance"}


def run_with_db(scenario):
    async def go():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                await scenario(db)
        finally:
            await engine.dispose()

    asyncio.run(go())


def test_data_status_ranks_a_failed_batch_over_late_data():
    fresh = {"freshness": "fresh", "last_failed": False}
    assert data_status(fresh, []) == OK
    assert data_status(fresh, ["Bank"]) == WARNING
    assert data_status({"freshness": "stale", "last_failed": False}, []) == WARNING
    assert data_status({"freshness": "never", "last_failed": False}, []) == WARNING
    assert data_status({**fresh, "last_failed": True}, []) == CRITICAL
    assert data_status({"freshness": "stale", "last_failed": True}, ["Bank"]) == CRITICAL


def feed(identifier, source, when):
    return SourceRecord(
        id=identifier,
        source_system=source,
        source_version="v1",
        source_record_id=identifier,
        delivery_id="d",
        payload=b"{}",
        payload_hash="h",
        received_at=when,
        source_event_time=when,
        status="Archived",
    )


def test_step_one_names_the_silent_feeds_and_a_failed_batch():
    async def scenario(db):
        db.add_all(
            [
                feed("p", "POS", NOW - timedelta(hours=2)),
                feed("c", "POSControl", NOW - timedelta(hours=2)),
                feed("o", "Processor", NOW - timedelta(hours=2)),
                feed("e", "ERP", NOW - timedelta(hours=2)),
                feed("s", "Shopify", NOW - timedelta(hours=2)),
                feed("b", "Bank", NOW - timedelta(days=6)),  # stale
                BatchRun(
                    id="r",
                    business_date=YESTERDAY,
                    status="Failed",
                    started_at=NOW - timedelta(hours=3),
                    finished_at=NOW - timedelta(hours=2),
                ),
            ]
        )
        await db.commit()
        data = (await operating_plan(db, FINANCE, NOW))["data"]
        assert data["status"] == CRITICAL and data["batch"]["last_failed"] is True
        assert data["silent_feeds"] == ["Bank"] and data["feeds"] == 6

    run_with_db(scenario)


def test_an_empty_system_is_a_warning_not_an_error():
    async def scenario(db):
        plan = await operating_plan(db, FINANCE, NOW)
        assert plan["data"]["status"] == WARNING
        assert plan["data"]["batch"]["freshness"] == "never"
        assert len(plan["data"]["silent_feeds"]) == 6
        assert plan["store_days"]["open"] == 0 and plan["store_days"]["ready_to_close"] == 0

    run_with_db(scenario)


def manifest(store, day):
    stamp = datetime.fromisoformat(f"{day}T00:00:00+00:00")
    payload = {"store_id": store, "business_date": day, "declared_count": 1, "declared_total": 5}
    return SourceRecord(
        id=f"m-{store}-{day}",
        source_system="POSControl",
        source_version="v1",
        source_record_id=f"m-{store}-{day}",
        delivery_id="d",
        payload=json.dumps(payload).encode(),
        payload_hash="h",
        received_at=stamp,
        source_event_time=stamp,
        status="Processed",
    )


def sale(identifier, store, day):
    stamp = datetime.fromisoformat(f"{day}T10:00:00+00:00")
    return CanonicalTransaction(
        id=identifier,
        business_date=day,
        transaction_date=stamp,
        event_timestamp=stamp,
        processing_timestamp=stamp,
        settlement_date=day,
        transaction_type="Sale",
        source_lineage="pos-map-v1",
        currency="USD",
        signed_amount=Decimal("5"),
        store_id=store,
        channel_id="store",
    )


def clean_day(identifier, store, day):
    """A store day with complete data and a balanced, current set of totals."""
    return [
        StoreDay(
            id=identifier, store_id=store, business_date=day, audit_version=1, status="Auditing"
        ),
        AuditTotal(
            id=f"t-{identifier}",
            store_day_id=identifier,
            audit_version=1,
            total_name="POS to GL Variance",
            level="Store",
            calculated_amount=Decimal("5"),
            declared_amount=Decimal("5"),
            variance_amount=Decimal("0"),
        ),
        manifest(store, day),
        sale(f"x-{identifier}", store, day),
    ]


def test_step_two_counts_what_can_be_closed_what_is_held_back_and_what_is_late():
    async def scenario(db):
        db.add_all(
            [
                FoundationReference(
                    id=f"f{n}", category="Store", code=f"S{n}", name=f"S{n}", active=True
                )
                for n in (1, 2, 3)
            ]
        )
        db.add_all(clean_day("ready", "S1", YESTERDAY))
        # S2 is held back: its totals have a variance nobody has explained.
        db.add_all(clean_day("held", "S2", YESTERDAY))
        await db.flush()
        (await db.get(AuditTotal, "t-held")).variance_amount = Decimal("3")
        # An old day that never closed is stuck.
        db.add(StoreDay(id="old", store_id="S1", business_date="2026-10-01", status="Auditing"))
        # S3 sent nothing for yesterday, so it is a late store.
        await db.commit()

        sd = (await operating_plan(db, FINANCE, NOW))["store_days"]
        assert sd["open"] == 3
        assert sd["ready_to_close"] == 1
        assert sd["needs_attention"] == 1
        assert sd["stuck"] == 1 and sd["stuck_after_days"] == 3
        assert (sd["expected_stores"], sd["late_stores"], sd["late_date"]) == (3, 1, YESTERDAY)
        assert sd["sampled"] is False and sd["examined"] == 3

    run_with_db(scenario)


def test_store_day_numbers_only_cover_the_users_stores():
    async def scenario(db):
        db.add_all(
            [
                FoundationReference(
                    id=f"f{n}", category="Store", code=f"S{n}", name=f"S{n}", active=True
                )
                for n in (1, 2)
            ]
        )
        db.add_all(clean_day("a", "S1", YESTERDAY))
        db.add_all(clean_day("b", "S2", YESTERDAY))
        await db.commit()

        everyone = (await operating_plan(db, FINANCE, NOW))["store_days"]
        manager = {"user": "m", "role": "analyst", "store_scoped": True, "stores": ["S2"]}
        mine = (await operating_plan(db, manager, NOW))["store_days"]
        assert (everyone["open"], everyone["expected_stores"]) == (2, 2)
        assert (mine["open"], mine["expected_stores"], mine["ready_to_close"]) == (1, 1, 1)
        nobody = {"user": "n", "role": "analyst", "store_scoped": True, "stores": []}
        assert (await operating_plan(db, nobody, NOW))["store_days"]["open"] == 0

    run_with_db(scenario)


def test_only_the_newest_open_days_are_examined_when_there_are_many(monkeypatch):
    async def scenario(db):
        monkeypatch.setattr(plan_module, "SAMPLE", 5)
        db.add_all(
            StoreDay(id=f"d{n}", store_id="S1", business_date=f"2026-09-{n:02d}", status="Auditing")
            for n in range(1, 13)
        )
        await db.commit()
        sd = (await operating_plan(db, FINANCE, NOW))["store_days"]
        assert (sd["open"], sd["examined"], sd["sampled"]) == (12, 5, True)

    run_with_db(scenario)
