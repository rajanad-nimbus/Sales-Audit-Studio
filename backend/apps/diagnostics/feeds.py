"""Health of each inbound data feed, worked out from the source records already received."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.ingest.models import SourceRecord

SOURCES = ("POS", "POSControl", "Processor", "Bank", "ERP", "Shopify")
ACCEPTED = ("Archived", "Processed")
STALE_AFTER = timedelta(hours=48)
MAX_REASONS = 20


def aware(value: datetime | None) -> datetime | None:
    """Databases without time zones hand back naive timestamps; they are UTC."""
    return value if value is None or value.tzinfo else value.replace(tzinfo=UTC)


def feed_state(last_accepted: datetime | None, now: datetime) -> str:
    if last_accepted is None:
        return "No data"
    return "Stale" if now - last_accepted > STALE_AFTER else "Current"


async def feed_status(db: AsyncSession, now: datetime | None = None) -> dict:
    """Per source: when data last arrived, how much came in lately, and what was set aside."""
    now = now or datetime.now(UTC)
    day_ago, week_ago = now - timedelta(days=1), now - timedelta(days=7)
    quarantined = SourceRecord.status == "Quarantined"
    rows = (
        await db.execute(
            select(
                SourceRecord.source_system,
                func.max(SourceRecord.received_at),
                func.max(case((SourceRecord.status.in_(ACCEPTED), SourceRecord.received_at))),
                func.sum(case((SourceRecord.received_at >= day_ago, 1), else_=0)),
                func.sum(case(((SourceRecord.received_at >= day_ago) & quarantined, 1), else_=0)),
                func.sum(case((quarantined, 1), else_=0)),
            ).group_by(SourceRecord.source_system)
        )
    ).all()
    seen = {row[0]: row for row in rows}
    feeds = []
    for source in SOURCES:
        _, last_received, last_accepted, received_24h, quarantined_24h, held = seen.get(
            source, (source, None, None, 0, 0, 0)
        )
        last_accepted = aware(last_accepted)
        feeds.append(
            {
                "source_system": source,
                "state": feed_state(last_accepted, now),
                "last_received_at": aware(last_received),
                "last_accepted_at": last_accepted,
                "received_24h": int(received_24h or 0),
                "quarantined_24h": int(quarantined_24h or 0),
                "quarantined_total": int(held or 0),
            }
        )
    reasons = (
        await db.execute(
            select(
                SourceRecord.source_system,
                SourceRecord.quarantine_reason,
                func.count().label("records"),
            )
            .where(quarantined, SourceRecord.received_at >= week_ago)
            .group_by(SourceRecord.source_system, SourceRecord.quarantine_reason)
            .order_by(func.count().desc(), SourceRecord.source_system)
            .limit(MAX_REASONS)
        )
    ).all()
    return {
        "as_of": now,
        "stale_after_hours": int(STALE_AFTER.total_seconds() // 3600),
        "feeds": feeds,
        "quarantine_reasons_7d": [
            {"source_system": source, "reason": reason or "No reason recorded", "records": count}
            for source, reason, count in reasons
        ],
    }
