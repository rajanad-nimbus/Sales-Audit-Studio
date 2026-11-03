"""The Command Center's operating plan: where today's work stands, from live data.

Step one is whether data has arrived (the daily batch and the source feeds). Step two is whether
the user's store days can be balanced: how many are ready to close, held back, stuck, or have a
store whose data is late. Store-day numbers respect the user's store scope; the batch and the
feeds are shared by every store.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.diagnostics.feeds import feed_status
from apps.operations.batch_routes import status as batch_status
from apps.security import scope
from apps.store_days.models import StoreDay
from apps.store_days.readiness import COMPLETE, late_stores, readiness

SAMPLE = 500  # newest open store days examined for readiness, so the plan stays quick
STUCK_AFTER_DAYS = 3
OK, WARNING, CRITICAL = "ok", "warning", "critical"


def data_status(batch: dict, silent: list[str]) -> str:
    """Critical if the last batch failed, a warning if data is late or missing, else ok."""
    if batch.get("last_failed"):
        return CRITICAL
    if batch["freshness"] in ("never", "stale") or silent:
        return WARNING
    return OK


async def operating_plan(db: AsyncSession, user: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    today = now.date()

    batch = await batch_status(db)
    feeds = (await feed_status(db, now))["feeds"]
    silent = [f["source_system"] for f in feeds if f["state"] != "Current"]
    data = {
        "status": data_status(batch, silent),
        "batch": {
            "freshness": batch["freshness"],
            "last_failed": batch["last_failed"],
            "hours_since_success": batch.get("hours_since_success"),
            "data_as_of": batch.get("data_as_of"),
        },
        "silent_feeds": silent,
        "feeds": len(feeds),
    }

    not_closed = StoreDay.status != "Closed"
    open_total = (
        await db.execute(
            scope.restrict(
                select(func.count()).select_from(StoreDay).where(not_closed),
                user,
                StoreDay.store_id,
            )
        )
    ).scalar_one()
    newest_open = list(
        (
            await db.execute(
                scope.restrict(
                    select(StoreDay)
                    .where(not_closed)
                    .order_by(StoreDay.business_date.desc(), StoreDay.store_id)
                    .limit(SAMPLE + 1),
                    user,
                    StoreDay.store_id,
                )
            )
        ).scalars()
    )
    sampled = len(newest_open) > SAMPLE
    newest_open = newest_open[:SAMPLE]
    views = await readiness(db, newest_open)
    cutoff = (today - timedelta(days=STUCK_AFTER_DAYS)).isoformat()
    late_date = (today - timedelta(days=1)).isoformat()
    expected, late = await late_stores(db, user, late_date)

    store_days = {
        "open": open_total,
        "examined": len(newest_open),
        "sampled": sampled,
        "ready_to_close": sum(1 for v in views.values() if v["ready_to_close"]),
        "data_incomplete": sum(1 for v in views.values() if v["ingest"]["state"] != COMPLETE),
        "needs_attention": sum(
            1 for v in views.values() if v["audit"]["state"] in ("Needs attention", "Out of date")
        ),
        "stuck": sum(1 for day in newest_open if day.business_date <= cutoff),
        "stuck_after_days": STUCK_AFTER_DAYS,
        "late_stores": len(late),
        "expected_stores": expected,
        "late_date": late_date,
    }
    return {"as_of": now, "data": data, "store_days": store_days}
