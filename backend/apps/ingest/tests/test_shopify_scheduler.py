"""When a daily or weekly Shopify sync is due, how far back it pulls, and what a schedule may contain."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.ingest.routes import SourceSettingPatch
from apps.ingest.shopify import scheduler as sch
from models import Base, SourceSetting

UTC = timezone.utc
# Sunday 1 Nov 2026, noon UTC (a Sunday: weekday() == 6)
NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def row(**kw):
    return SimpleNamespace(**{"enabled": True, "sync_frequency": "daily", "sync_time": "02:00", "sync_weekday": 0,
                              "sync_timezone": "UTC", "last_scheduled_run_at": NOW - timedelta(days=2), **kw})


def test_nothing_is_scheduled_when_off_or_incomplete():
    assert not sch.is_due(None, NOW) and sch.next_run(None, NOW) is None
    assert not sch.is_due(row(sync_frequency=None), NOW)
    assert not sch.is_due(row(enabled=False), NOW)
    assert not sch.is_due(row(sync_time=None), NOW)


def test_a_daily_run_is_due_once_the_time_has_passed_since_the_last_run():
    assert sch.is_due(row(last_scheduled_run_at=NOW - timedelta(days=2)), NOW)
    assert not sch.is_due(row(last_scheduled_run_at=datetime(2026, 11, 1, 2, 0, 1, tzinfo=UTC)), NOW)   # already ran today
    assert not sch.is_due(row(sync_time="13:00", last_scheduled_run_at=datetime(2026, 10, 31, 13, 0, 1, tzinfo=UTC)), NOW)  # 13:00 is still ahead


def test_a_schedule_only_counts_from_when_it_was_saved():
    saved_at = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)          # saved after today's 02:00 passed
    assert not sch.is_due(row(last_scheduled_run_at=saved_at), NOW)
    assert not sch.is_due(row(last_scheduled_run_at=None), NOW)  # claimed (stamped) on the first tick instead


def test_a_weekly_run_waits_for_its_weekday():
    monday = row(sync_frequency="weekly", sync_weekday=0, last_scheduled_run_at=datetime(2026, 10, 26, 3, 0, tzinfo=UTC))
    assert sch.is_due(monday, datetime(2026, 11, 2, 2, 5, tzinfo=UTC))           # Monday 02:05, after 02:00
    assert not sch.is_due(monday, datetime(2026, 11, 2, 1, 55, tzinfo=UTC))      # Monday but before 02:00
    assert not sch.is_due(monday, datetime(2026, 11, 1, 12, 0, tzinfo=UTC))      # Sunday
    assert sch.next_run(monday, NOW) == datetime(2026, 11, 2, 2, 0, tzinfo=UTC)


def test_the_time_of_day_is_in_the_chosen_time_zone():
    london_winter = row(sync_timezone="Asia/Kathmandu", sync_time="08:00")       # UTC+5:45
    assert sch.next_run(london_winter, NOW) == datetime(2026, 11, 2, 2, 15, tzinfo=UTC)


def test_next_run_is_the_next_future_occurrence():
    assert sch.next_run(row(), NOW) == datetime(2026, 11, 2, 2, 0, tzinfo=UTC)
    assert sch.next_run(row(sync_time="13:00"), NOW) == datetime(2026, 11, 1, 13, 0, tzinfo=UTC)


def test_the_window_covers_two_periods_within_a_day_to_a_week():
    assert sch.lookback(row()) == timedelta(days=2)
    assert sch.lookback(row(sync_frequency="weekly")) == timedelta(days=7)


def test_describe_reads_naturally():
    assert sch.describe(row()) == "Every day at 02:00 UTC"
    assert sch.describe(row(sync_frequency="weekly", sync_weekday=4)) == "Every Friday at 02:00 UTC"
    assert sch.describe(row(sync_frequency=None)) == "Off"


def test_a_schedule_must_be_well_formed():
    assert SourceSettingPatch(sync_frequency="weekly", sync_time="23:59", sync_weekday=6, sync_timezone="Europe/London")
    for bad in ({"sync_time": "25:00"}, {"sync_time": "9:00"}, {"sync_weekday": 7}, {"sync_timezone": "Mars/Base"}, {"sync_frequency": "hourly"}):
        with pytest.raises(ValidationError):
            SourceSettingPatch(**bad)


def test_run_outcome_does_not_overwrite_a_schedule_changed_while_sync_was_running():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            claimed_at = NOW - timedelta(minutes=5)
            async with sessions() as db:
                db.add(SourceSetting(
                    source_system="Shopify", sync_frequency="daily", sync_time="02:00",
                    last_scheduled_run_at=NOW, last_scheduled_status=None,
                ))
                await db.commit()
                assert not await sch._record_outcome(db, claimed_at, "Succeeded", "old run")
                await db.rollback()
                row = (await db.execute(select(SourceSetting))).scalar_one()
                assert row.last_scheduled_run_at.replace(tzinfo=UTC) == NOW
                assert row.last_scheduled_status is None

                await db.execute(update(SourceSetting).values(last_scheduled_status="Running"))
                assert await sch._record_outcome(db, NOW, "Succeeded", "current run")
                await db.commit()
                await db.refresh(row)
                assert row.last_scheduled_status == "Succeeded"
                assert row.last_scheduled_message == "current run"
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_final_source_check_stops_ingest_after_source_is_disabled():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with sessions() as db:
                db.add(SourceSetting(source_system="Shopify", enabled=False))
                await db.commit()
                with pytest.raises(sch.SourceDisabledDuringSync, match="switched off"):
                    await sch._lock_enabled_source(db)
        finally:
            await engine.dispose()

    asyncio.run(run())
