"""Diagnostics: each health check in both states, feed health, audit activity and access."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException
from integrations.ontology.nimbus import audit
from models import (
    AuditEvent,
    Base,
    BatchRun,
    CanonicalTransaction,
    Case,
    ExportBatch,
    ExportDestination,
    SourceRecord,
    StoreDay,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.diagnostics import health as health_module
from apps.diagnostics.activity import audit_activity
from apps.diagnostics.feeds import feed_state, feed_status
from apps.diagnostics.health import CRITICAL, OK, WARNING, system_health, worst
from apps.diagnostics.integrity import verify_window
from apps.diagnostics.routes import system_wide

NOW = datetime(2026, 10, 10, 12, tzinfo=UTC)


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


def by_name(result: dict) -> dict:
    return {c["name"]: c for c in result["checks"]}


def record(identifier, source, status, received, reason=None):
    return SourceRecord(
        id=identifier,
        source_system=source,
        source_version="v1",
        source_record_id=identifier,
        delivery_id="d",
        payload=b"{}",
        payload_hash="h",
        received_at=received,
        source_event_time=received,
        status=status,
        quarantine_reason=reason,
    )


def test_worst_status_wins():
    assert worst([{"status": OK}, {"status": WARNING}]) == WARNING
    assert worst([{"status": WARNING}, {"status": CRITICAL}, {"status": OK}]) == CRITICAL
    assert worst([]) == OK


def test_an_empty_system_is_flagged_for_what_has_never_happened():
    async def scenario(db):
        checks = by_name(await system_health(db, NOW))
        assert (
            checks["Daily batch"]["status"] == WARNING and checks["Daily batch"]["value"] == "never"
        )
        assert checks["Data feeds"]["status"] == WARNING and checks["Data feeds"]["value"] == 6
        assert checks["Quarantined records"]["status"] == OK
        assert checks["Failed exports"]["status"] == OK
        assert checks["Database"]["status"] == OK

    run_with_db(scenario)


def test_the_schema_check_compares_the_database_revision_with_the_code(monkeypatch):
    async def scenario(db):
        monkeypatch.setattr(health_module, "expected_revision", lambda: "0080")
        unreadable = by_name(await system_health(db, NOW))["Database schema"]
        assert unreadable["status"] == WARNING  # no alembic_version table here

        from sqlalchemy import text

        await db.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
        await db.execute(text("INSERT INTO alembic_version VALUES ('0079')"))
        behind = by_name(await system_health(db, NOW))["Database schema"]
        assert (
            behind["status"] == CRITICAL
            and "0079" in behind["detail"]
            and "0080" in behind["detail"]
        )

        await db.execute(text("UPDATE alembic_version SET version_num = '0080'"))
        assert by_name(await system_health(db, NOW))["Database schema"]["status"] == OK

    run_with_db(scenario)


def test_failed_batches_and_exports_are_critical_and_old_work_is_a_warning():
    async def scenario(db):
        db.add_all(
            [
                BatchRun(
                    id="b1",
                    business_date="2026-10-09",
                    status="Failed",
                    started_at=NOW - timedelta(hours=2),
                    finished_at=NOW - timedelta(hours=1),
                    error="boom",
                ),
                ExportDestination(id="d", name="ERP", kind="file", config={}),
            ]
        )
        await db.flush()
        for identifier, status, sent in (
            ("e1", "Failed", None),
            ("e2", "Sent", NOW - timedelta(days=3)),
            ("e3", "Sent", NOW - timedelta(hours=1)),
        ):
            db.add(
                ExportBatch(
                    id=identifier,
                    destination_id="d",
                    dataset="adjustments",
                    status=status,
                    idempotency_key=identifier,
                    created_by="it",
                    sent_at=sent,
                )
            )
        db.add(StoreDay(id="s", store_id="S1", business_date="2026-10-09", totals_stale=True))
        db.add(
            Case(
                id="c",
                case_number="C1",
                case_type="X",
                business_date="2026-10-01",
                store_id="S1",
                status="Open",
                sla_due_at=NOW - timedelta(hours=3),
            )
        )
        stamp = NOW - timedelta(days=5)
        db.add(
            CanonicalTransaction(
                id="t",
                business_date="2026-10-04",
                transaction_date=stamp,
                event_timestamp=stamp,
                processing_timestamp=stamp,
                settlement_date="2026-10-04",
                transaction_type="Sale",
                source_lineage="pos-map-v1",
                currency="USD",
                signed_amount=Decimal("5"),
                store_id="S1",
                channel_id="store",
                reconciliation_status="Unmatched",
            )
        )
        await db.commit()

        result = await system_health(db, NOW)
        checks = by_name(result)
        assert checks["Daily batch"]["status"] == CRITICAL
        assert (
            checks["Failed exports"]["status"] == CRITICAL
            and checks["Failed exports"]["value"] == 1
        )
        # Only the batch sent three days ago counts as unacknowledged; the recent one is fine.
        assert checks["Unacknowledged exports"]["value"] == 1
        assert checks["Out-of-date totals"]["value"] == 1
        assert checks["Cases past their due time"]["value"] == 1
        assert checks["Reconciliation backlog"]["value"] == 1
        assert result["status"] == CRITICAL

    run_with_db(scenario)


def test_feed_health_separates_silent_current_and_quarantined_feeds():
    async def scenario(db):
        db.add_all(
            [
                record("p1", "POS", "Archived", NOW - timedelta(hours=3)),
                record("p2", "POS", "Quarantined", NOW - timedelta(hours=2), "invalid amount"),
                record("p3", "POS", "Quarantined", NOW - timedelta(days=3), "invalid amount"),
                record("b1", "Bank", "Processed", NOW - timedelta(days=5)),
                record("e1", "ERP", "Quarantined", NOW - timedelta(hours=1), "missing store_id"),
            ]
        )
        await db.commit()
        result = await feed_status(db, NOW)
        feeds = {f["source_system"]: f for f in result["feeds"]}

        assert feeds["POS"]["state"] == "Current"
        assert (feeds["POS"]["received_24h"], feeds["POS"]["quarantined_24h"]) == (2, 1)
        assert feeds["POS"]["quarantined_total"] == 2
        assert feeds["Bank"]["state"] == "Stale"  # last accepted five days ago
        # A feed that only ever sent bad records has nothing accepted.
        assert feeds["ERP"]["state"] == "No data" and feeds["ERP"]["quarantined_24h"] == 1
        assert (
            feeds["Shopify"]["state"] == "No data" and feeds["Shopify"]["last_received_at"] is None
        )
        reasons = {
            (r["source_system"], r["reason"]): r["records"] for r in result["quarantine_reasons_7d"]
        }
        assert reasons[("POS", "invalid amount")] == 2
        assert reasons[("ERP", "missing store_id")] == 1

    run_with_db(scenario)


def test_feed_state_boundaries():
    assert feed_state(None, NOW) == "No data"
    assert feed_state(NOW - timedelta(hours=47), NOW) == "Current"
    assert feed_state(NOW - timedelta(hours=49), NOW) == "Stale"


def test_audit_activity_counts_people_and_lists_the_changes_that_matter():
    async def scenario(db):
        for index in range(6):
            audit(db, "CASE_NOTE_ADDED", None, "Case", f"c{index}", "noted", actor="analyst.one")
        audit(
            db,
            "TOTAL_DEFINITION_ACTIVATED",
            None,
            "AuditTotalDefinition",
            "d1",
            "Activated Sales r2",
            actor="approver",
        )
        audit(
            db,
            "PERIOD_CLOSED",
            None,
            "PeriodClose",
            "p1",
            "Closed through 2026-09-30",
            actor="finance",
        )
        audit(
            db,
            "STORE_DAY_REOPENED",
            None,
            "StoreDay",
            "s1",
            "Reopened: late deposit",
            actor="finance",
        )
        await db.commit()
        # SQLite has no hash-chain trigger, so give the events the sequence numbers it would.
        for number, event in enumerate(
            (await db.execute(AuditEvent.__table__.select().order_by(AuditEvent.created_at))), 1
        ):
            await db.execute(
                AuditEvent.__table__.update().where(AuditEvent.id == event.id).values(seq=number)
            )
        await db.commit()

        result = await audit_activity(db, 1000)
        assert result["window"]["events"] == 9
        people = {row["actor"]: row for row in result["by_actor"]}
        assert (people["analyst.one"]["events"], people["analyst.one"]["sensitive"]) == (6, 0)
        assert (people["finance"]["events"], people["finance"]["sensitive"]) == (2, 2)
        assert [r["event_type"] for r in result["sensitive"]] == [
            "STORE_DAY_REOPENED",
            "PERIOD_CLOSED",
            "TOTAL_DEFINITION_ACTIVATED",
        ]
        assert result["by_type"][0] == {"event_type": "CASE_NOTE_ADDED", "events": 6}

        # A small window looks only at the newest events.
        newest = await audit_activity(db, 100)
        assert newest["window"]["events"] == 9
        assert newest["window"]["from_seq"] == 1

    run_with_db(scenario)


def test_audit_activity_on_an_empty_trail():
    async def scenario(db):
        assert await audit_activity(db, 100) == {
            "window": {"events": 0},
            "by_actor": [],
            "by_type": [],
            "sensitive": [],
        }

    run_with_db(scenario)


def test_the_hash_chain_is_only_verified_where_the_database_computes_it():
    async def scenario(db):
        result = await verify_window(db, None, 5000)
        assert result["supported"] is False and "PostgreSQL" in result["reason"]

    run_with_db(scenario)


def test_diagnostics_are_closed_to_users_limited_to_some_stores():
    assert system_wide({"user": "finance"}) == {"user": "finance"}
    with pytest.raises(HTTPException) as refused:
        system_wide({"user": "m", "store_scoped": True, "stores": ["S1"]})
    assert refused.value.status_code == 403
