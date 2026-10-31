"""Scheduled Shopify sync.

The nightly batch still relies on an external cron. This is the in-app schedule for Shopify: IT picks daily or weekly
and a time of day in Data Pipeline, Sources, and a loop started with the application pulls recent orders and refunds
when a run is due. Ingest de-duplicates by record identity, so overlapping windows are safe. A database lock keeps two
copies of the application from running the same sync.
"""
import asyncio
import logging
import os
import uuid
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, text, update

from apps.ingest import service as ingest
from apps.ingest import source_settings
from apps.ingest.shopify import adapter as shopify_adapter
from database import async_session
from integrations.ontology.nimbus import audit
from model_base import utcnow
from apps.ingest.models import SourceSetting

log = logging.getLogger(__name__)

SOURCE = "Shopify"
FREQUENCIES = ("daily", "weekly")
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
TICK_SECONDS = 60
LOCK_KEY = "shopify-scheduled-sync"
ACTOR = "shopify-scheduler"


class SourceDisabledDuringSync(Exception):
    """The source was switched off while the connector was fetching its feed."""


def safe_failure_message(error: Exception) -> str:
    """Return a persisted sync status message without exception payload details."""
    if isinstance(error, SourceDisabledDuringSync):
        return "Shopify source disabled during sync"
    return shopify_adapter.safe_failure_message(error)


def parse_time(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def zone(row) -> ZoneInfo:
    return ZoneInfo(row.sync_timezone or "UTC")


def scheduled(row) -> bool:
    return bool(row is not None and row.enabled and row.sync_frequency in FREQUENCIES and row.sync_time)


def _occurrence(row, day) -> datetime:
    return datetime.combine(day, parse_time(row.sync_time), tzinfo=zone(row))


def _matches_day(row, day) -> bool:
    return row.sync_frequency == "daily" or day.weekday() == (row.sync_weekday or 0)


def previous_run(row, now: datetime) -> datetime | None:
    """The latest scheduled moment at or before now."""
    if not scheduled(row):
        return None
    today = now.astimezone(zone(row)).date()
    for back in range(0, 8):
        day = today - timedelta(days=back)
        if _matches_day(row, day) and _occurrence(row, day) <= now:
            return _occurrence(row, day)
    return None


def next_run(row, now: datetime) -> datetime | None:
    """The first scheduled moment after now."""
    if not scheduled(row):
        return None
    today = now.astimezone(zone(row)).date()
    for ahead in range(0, 9):
        day = today + timedelta(days=ahead)
        if _matches_day(row, day) and _occurrence(row, day) > now:
            return _occurrence(row, day)
    return None


def is_due(row, now: datetime) -> bool:
    """Due when a scheduled moment has passed since the last run. A schedule only counts from when it was saved."""
    if not scheduled(row) or row.last_scheduled_run_at is None:
        return False
    due_at = previous_run(row, now)
    return due_at is not None and row.last_scheduled_run_at < due_at


def lookback(row) -> timedelta:
    """How far back to pull: two periods, so a missed run or a late refund is still caught, within one day to one week."""
    period = timedelta(days=7 if row.sync_frequency == "weekly" else 1)
    return min(max(period * 2, timedelta(days=1)), timedelta(days=7))


def describe(row) -> str:
    if not scheduled(row):
        return "Off"
    when = f"{row.sync_time} {row.sync_timezone or 'UTC'}"
    return f"Every day at {when}" if row.sync_frequency == "daily" else f"Every {WEEKDAYS[row.sync_weekday or 0]} at {when}"


async def _claim(session_factory, now: datetime) -> tuple[datetime, datetime] | None:
    """Under a lock, decide whether a run is due and mark it started, so no other copy picks it up. Returns the window."""
    async with session_factory() as db:
        got = (await db.execute(text("SELECT pg_try_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": LOCK_KEY})).scalar()
        if not got or not source_settings.shopify_configured():
            return None
        row = (await db.execute(select(SourceSetting).where(SourceSetting.source_system == SOURCE))).scalar_one_or_none()
        if not scheduled(row):
            return None
        if row.last_scheduled_run_at is None:   # a schedule counts from when it was set, never backfills
            row.last_scheduled_run_at = now
            await db.commit()
            return None
        if not is_due(row, now):
            return None
        row.last_scheduled_run_at, row.last_scheduled_status, row.last_scheduled_message = now, "Running", None
        window = (now - lookback(row), now)
        await db.commit()
        return window


async def _record_outcome(db, claimed_at: datetime, status: str, message: str) -> bool:
    """Update status only while the schedule still belongs to this claimed run."""
    updated = (await db.execute(
        update(SourceSetting)
        .where(
            SourceSetting.source_system == SOURCE,
            SourceSetting.last_scheduled_run_at == claimed_at,
            SourceSetting.last_scheduled_status == "Running",
        )
        .values(last_scheduled_status=status, last_scheduled_message=message[:255])
        .returning(SourceSetting.source_system)
        .execution_options(synchronize_session=False)
    )).scalar_one_or_none()
    return updated is not None


async def _lock_enabled_source(db) -> None:
    """Serialize the final enabled check with source-setting updates through ingest commit."""
    row = (await db.execute(
        select(SourceSetting)
        .where(SourceSetting.source_system == SOURCE)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )).scalar_one_or_none()
    if row is not None and not row.enabled:
        raise SourceDisabledDuringSync("Shopify was switched off while the scheduled sync was fetching data")


async def run_if_due(session_factory=async_session, now: datetime | None = None) -> str | None:
    """Run the scheduled Shopify sync if it is due. Returns the outcome, or None when nothing was due."""
    now = now or utcnow()
    window = await _claim(session_factory, now)
    if window is None:
        return None
    store_id = os.getenv("SHOPIFY_STORE_ID", "SHOPIFY-MAIN")
    status, message = "Succeeded", ""
    needs_outcome_record = False
    async with session_factory() as db:
        try:
            feed = await shopify_adapter.fetch_shopify_feed(store_id, created_at_min=window[0], created_at_max=window[1])
            await _lock_enabled_source(db)
            delivery = f"SHOPIFY-{now.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"   # same shape as a manual sync; fits the audit column
            stats = await ingest.ingest(db, SOURCE, delivery, feed.get(SOURCE, []))
            message = f"{stats['received']} received, {stats['canonical_created']} new, {stats['quarantined']} quarantined"
            audit(db, "SHOPIFY_SCHEDULED_SYNC", None, "ShopifySync", delivery, f"Scheduled Shopify sync: {message}", actor=ACTOR)
            await _record_outcome(db, now, status, message)
            await db.commit()
        except Exception as exc:  # a failed run is recorded and retried at the next scheduled time; it must never stop the loop
            await db.rollback()
            status = "Skipped" if isinstance(exc, SourceDisabledDuringSync) else "Failed"
            message = safe_failure_message(exc)
            needs_outcome_record = True
            log.warning("Scheduled Shopify sync failed: %s", message)
    if needs_outcome_record:
        async with session_factory() as db:
            claimed = await _record_outcome(db, now, status, message)
            if claimed:
                event = "SHOPIFY_SCHEDULED_SYNC_FAILED" if status == "Failed" else "SHOPIFY_SCHEDULED_SYNC_SKIPPED"
                audit(db, event, None, "ShopifySync", SOURCE, f"Scheduled Shopify sync {status.lower()}: {message}"[:500], actor=ACTOR)
            await db.commit()
    return status


async def loop(session_factory=async_session, tick: float = TICK_SECONDS) -> None:
    """Check every minute whether a scheduled sync is due. Waits first, so start-up stays quick."""
    while True:
        await asyncio.sleep(tick)
        try:
            await run_if_due(session_factory)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Shopify scheduler tick failed")


def enabled() -> bool:
    return os.getenv("NIMBUS_SCHEDULER", "1") != "0"
