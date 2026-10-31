"""Queries for archived POS control manifests."""

from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select

from apps.ingest.models import SourceRecord

MANIFEST_PAGE_SIZE = 500


def validate_business_date(value: str) -> str:
    """Require a canonical ISO calendar date for a business-day operation."""
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError("business_date must be a valid YYYY-MM-DD date") from None
    return value


def manifest_query(business_date: str | None, statuses: tuple[str, ...]):
    """Select manifests by their recorded UTC business-date timestamp when a date is supplied."""
    query = select(SourceRecord).where(
        SourceRecord.source_system == "POSControl", SourceRecord.status.in_(statuses)
    )
    if business_date is not None:
        day = date.fromisoformat(validate_business_date(business_date))
        start = datetime.combine(day, time.min, tzinfo=timezone.utc)
        query = query.where(SourceRecord.source_event_time >= start)
        if day != date.max:
            query = query.where(SourceRecord.source_event_time < start + timedelta(days=1))
    return query


async def iter_manifests(db, business_date: str | None, statuses: tuple[str, ...]):
    """Yield matching manifests in bounded primary-key pages."""
    last_id = ""
    while True:
        page = (await db.execute(
            manifest_query(business_date, statuses)
            .where(SourceRecord.id > last_id)
            .order_by(SourceRecord.id)
            .limit(MANIFEST_PAGE_SIZE)
        )).scalars().all()
        if not page:
            return
        for manifest in page:
            yield manifest
        last_id = page[-1].id
