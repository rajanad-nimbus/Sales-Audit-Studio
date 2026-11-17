"""Scheduled store-day lifecycle automation."""

import logging
import uuid
from datetime import UTC, date, datetime, timedelta

from fastapi import HTTPException
from integrations.ontology.nimbus import audit
from model_base import utcnow
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from apps.controls.foundation.models import FoundationReference
from apps.ingest import reconciliation
from apps.ingest.manifests import validate_business_date
from apps.store_days.freshness import lock_configuration, lock_store_days
from apps.store_days.models import StoreDay, StoreDayClosureRequest
from apps.store_days.readiness import CLEAN, COMPLETE, NO_DATA, ingest_states, readiness
from apps.system.options import automatic_store_day_close_enabled
from apps.transactions.models import CanonicalTransaction

AGENT = "store-day-agent"
SWEEP_CHUNK = 250
MAX_SWEEP_DAYS = 20_000
SIGNOFF_RATIONALE = (
    "Automatic sign-off: data matches the source control total, and the current totals carry "
    "no open errors, unexplained variances or transaction number gaps"
)
logger = logging.getLogger(__name__)


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


async def run_store_day_agent(
    db: AsyncSession, business_date: str | None = None, *, store_ids: set[str] | None = None
) -> dict:
    """Open, total and close store days without a person, leaving only the exceptions.

    Opens a store day for every store with transactions on ``business_date``. Then, for every
    store day not yet closed, totals it when its totals are missing or out of date, and signs
    off and closes it when its data matches the source control total and nothing is open
    against it. A day that fails any check is left as it is for a person to work.

    Each store day is its own transaction, so one failure never holds back the others.
    """
    result = {"opened": 0, "totaled": 0, "closed": 0, "needs_review": 0, "failed": 0}
    if business_date is not None:
        validate_business_date(business_date)
        result["opened"] = await open_days_with_data(db, business_date, store_ids)
        await db.commit()
    result["automatic_close"] = close = await automatic_store_day_close_enabled(db)

    query = select(StoreDay.store_id, StoreDay.business_date).where(StoreDay.status != "Closed")
    if store_ids is not None:
        query = query.where(StoreDay.store_id.in_(store_ids))
    keys = [
        tuple(row)
        for row in await db.execute(
            query.order_by(StoreDay.business_date, StoreDay.store_id).limit(MAX_SWEEP_DAYS)
        )
    ]
    today = datetime_today_utc().isoformat()
    for offset in range(0, len(keys), SWEEP_CHUNK):
        chunk = list(
            (
                await db.execute(
                    select(StoreDay)
                    .where(
                        tuple_(StoreDay.store_id, StoreDay.business_date).in_(
                            keys[offset : offset + SWEEP_CHUNK]
                        ),
                        StoreDay.status != "Closed",
                    )
                    .execution_options(populate_existing=True)
                )
            ).scalars()
        )
        ingest = await ingest_states(db, [(day.store_id, day.business_date) for day in chunk])
        views = await readiness(db, chunk) if close else {}
        # Identify the work before any commit, so no loaded row is read after it expires.
        work = []
        for day in chunk:
            has_data = ingest[(day.store_id, day.business_date)]["state"] != NO_DATA
            needs_totals = has_data and (day.audit_version < 1 or day.totals_stale)
            closable = (
                close and not day.reopened_by and day.business_date < today and ready(views[day.id])
            )
            if needs_totals or closable:
                work.append((day.store_id, day.business_date))
            elif has_data:
                result["needs_review"] += 1
        for store_id, day_date in work:
            try:
                outcome = await settle_day(db, store_id, day_date, close=close)
                await db.commit()
            except HTTPException as exc:
                await db.rollback()
                logger.info("Store day %s/%s left for review: %s", store_id, day_date, exc.detail)
                outcome = {"closed": False}
            except Exception:
                await db.rollback()
                logger.exception("Store-day agent failed on %s/%s", store_id, day_date)
                result["failed"] += 1
                continue
            result["totaled"] += int(outcome.get("totaled", False))
            result["closed" if outcome["closed"] else "needs_review"] += 1
    return result


def ready(view: dict) -> bool:
    """Data matches the source control total, and nothing is open against the current totals."""
    return view["ingest"]["state"] == COMPLETE and view["audit"]["state"] == CLEAN


async def open_days_with_data(
    db: AsyncSession, business_date: str, store_ids: set[str] | None = None
) -> int:
    """Open a store day for every store that has transactions on the date and no store day yet."""
    query = (
        select(CanonicalTransaction.store_id)
        .where(
            CanonicalTransaction.business_date == business_date,
            CanonicalTransaction.store_id.is_not(None),
        )
        .distinct()
    )
    if store_ids is not None:
        query = query.where(CanonicalTransaction.store_id.in_(store_ids))
    stores = {store for store in (await db.execute(query)).scalars() if store}
    if not stores:
        return 0
    await lock_configuration(db)
    identities = {(store_id, business_date) for store_id in stores}
    existing = await lock_store_days(db, identities)
    opened = 0
    for store_id, day_date in sorted(identities - set(existing)):
        day = StoreDay(
            id=str(uuid.uuid4()), store_id=store_id, business_date=day_date, status="Open"
        )
        db.add(day)
        audit(
            db,
            "STORE_DAY_OPENED",
            None,
            "StoreDay",
            day.id,
            f"Store day {store_id}/{day_date} automatically opened when its data arrived",
            actor=AGENT,
            after={"store_id": store_id, "business_date": day_date, "trigger": "data"},
        )
        opened += 1
    return opened


async def settle_day(db: AsyncSession, store_id: str, business_date: str, *, close: bool) -> dict:
    """Total one store day if it needs it, then close it if it is clean. Does not commit."""
    from apps.store_days.closures import close_blocker, earlier_open_day
    from apps.store_days.routes import retotal_day
    from apps.store_days.service import get_day

    # Same lock order as a person closing the day: reconciliation, then the store day.
    await reconciliation.lock_reconciliation(db)
    day = await get_day(db, store_id, business_date, lock=True)
    if day.status == "Closed":
        return {"closed": True}
    totaled = day.audit_version < 1 or day.totals_stale
    if totaled:
        await retotal_day(db, store_id, business_date, AGENT)
    outcome = {"totaled": totaled, "closed": False}
    # A person who reopened a day has taken it over; the agent does not close it again.
    if not close or day.reopened_by or business_date >= datetime_today_utc().isoformat():
        return outcome
    view = (await readiness(db, [day]))[day.id]
    if not ready(view) or await close_blocker(db, day) or await earlier_open_day(db, day):
        return outcome

    now = utcnow()
    if not view["audit"]["signed_off"]:
        day.audit_signed_version = day.audit_version
        day.audit_signed_by, day.audit_signed_at = AGENT, now
        day.audit_signoff_rationale = SIGNOFF_RATIONALE
        audit(
            db,
            "STORE_DAY_AUDIT_SIGNED_OFF",
            None,
            "StoreDay",
            day.id,
            f"Totals version {day.audit_version} signed off: {SIGNOFF_RATIONALE}",
            actor=AGENT,
            after={"trigger": "automation"},
        )
    request = (
        (
            await db.execute(
                select(StoreDayClosureRequest)
                .where(
                    StoreDayClosureRequest.store_day_id == day.id,
                    StoreDayClosureRequest.status == "Pending",
                )
                .order_by(StoreDayClosureRequest.created_at.desc())
            )
        )
        .scalars()
        .first()
    )
    if request is None:
        request = StoreDayClosureRequest(
            id=str(uuid.uuid4()),
            store_day_id=day.id,
            requested_by=AGENT,
            evidence_basis=(
                f"Automatic close: {view['ingest']['detail']}; "
                f"{view['ingest']['transactions']} transaction(s); totals version "
                f"{day.audit_version} clean"
            ),
        )
        db.add(request)
    request.status, request.reviewed_by, request.reviewed_at = "Approved", AGENT, now
    day.status, day.closed_at, day.closed_by = "Closed", now, AGENT
    audit(
        db,
        "STORE_DAY_CLOSED",
        None,
        "StoreDay",
        day.id,
        f"Store day {store_id}/{business_date} automatically closed",
        actor=AGENT,
        after={"trigger": "automation", "audit_version": day.audit_version},
    )
    outcome["closed"] = True
    return outcome
    await open_following_day(db, store_id, business_date)


async def open_following_day(
    db: AsyncSession, store_id: str, closed_date: str, *, actor: str = AGENT
) -> bool:
    """Open the day after a day that was just closed, so the store always has a current day.

    Never opens a day in the future. A past day with no data is left unopened, so a backfill does
    not leave a run of empty open days; today's day always opens, as its data has yet to arrive.
    """
    next_day = date.fromisoformat(closed_date) + timedelta(days=1)
    today = datetime_today_utc()
    if next_day > today:
        return False
    next_date = next_day.isoformat()
    if next_day < today:
        state = (await ingest_states(db, [(store_id, next_date)]))[(store_id, next_date)]
        if state["state"] == NO_DATA:
            return False
    existing = await lock_store_days(db, {(store_id, next_date)})
    if (store_id, next_date) in existing:
        return False
    day = StoreDay(
        id=str(uuid.uuid4()), store_id=store_id, business_date=next_date, status="Open"
    )
    db.add(day)
    audit(
        db,
        "STORE_DAY_OPENED",
        None,
        "StoreDay",
        day.id,
        f"Store day {store_id}/{next_date} automatically opened after {closed_date} was closed",
        actor=actor,
        after={"store_id": store_id, "business_date": next_date, "trigger": "previous_day_closed"},
    )
    return True
