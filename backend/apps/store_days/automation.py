"""Scheduled store-day lifecycle automation."""

import uuid
from datetime import UTC, date, datetime, timedelta

from integrations.ontology.nimbus import audit
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.controls.foundation.models import FoundationReference
from apps.store_days.freshness import lock_configuration, lock_store_days
from apps.store_days.models import StoreDay


async def open_next_business_day(
    db: AsyncSession, processed_date: str, *, actor: str = "batch-scheduler"
) -> dict:
    """Idempotently open today for each active store after scheduled processing yesterday."""
    next_date = (date.fromisoformat(processed_date) + timedelta(days=1)).isoformat()
    if date.fromisoformat(next_date) != datetime_today_utc():
        return {
            "status": "Skipped",
            "opened": 0,
            "already_open": 0,
            "detail": "Next business date is not today",
        }

    await lock_configuration(db)
    stores = list(
        (
            await db.execute(
                select(FoundationReference.code)
                .where(
                    FoundationReference.category == "Store",
                    FoundationReference.active.is_(True),
                )
                .order_by(FoundationReference.code)
            )
        )
        .scalars()
        .all()
    )
    if not stores:
        return {
            "status": "Skipped",
            "opened": 0,
            "already_open": 0,
            "detail": "No active stores in Foundation Data",
        }
    identities = {(store_id, next_date) for store_id in stores}
    existing = await lock_store_days(db, identities)
    opened = 0
    for store_id, business_date in sorted(identities):
        if (store_id, business_date) in existing:
            continue
        day = StoreDay(
            id=str(uuid.uuid4()),
            store_id=store_id,
            business_date=business_date,
            status="Open",
        )
        db.add(day)
        audit(
            db,
            "STORE_DAY_OPENED",
            None,
            "StoreDay",
            day.id,
            f"Store day {store_id}/{business_date} automatically opened after scheduled processing",
            actor=actor,
            after={"store_id": store_id, "business_date": business_date, "trigger": "schedule"},
        )
        opened += 1
    return {
        "status": "Succeeded",
        "opened": opened,
        "already_open": len(identities) - opened,
        "detail": None,
    }


def datetime_today_utc() -> date:
    """The batch service uses UTC business dates unless a store-specific calendar is configured."""
    return datetime.now(UTC).date()
