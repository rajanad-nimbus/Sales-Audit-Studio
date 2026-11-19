"""The store-day agent opens, totals and closes clean store days and leaves the exceptions."""

import asyncio
from datetime import UTC, date, datetime, timedelta

from apps.ingest import auto_audit, reconciliation
from apps.ingest import service as ingest
from apps.store_days import automation
from apps.store_days import routes as sd
from apps.store_days.closures import ReopenRequest, reopen_store_day
from apps.system.models import SystemOption
from apps.system.options import AUTO_AUDIT_AFTER_IMPORT_KEY, AUTO_STORE_DAY_CLOSE_KEY
from models import AuditEvent, Base, Case, StoreDay, StoreDayClosureRequest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

DATE = (datetime.now(UTC).date() - timedelta(days=3)).isoformat()
FINANCE = {"user": "finance.user", "role": "finance"}


def run_with_db(scenario):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                await scenario(db)
        finally:
            await engine.dispose()

    asyncio.run(run())


def shopify_feed(store: str, *, reported_orders: int = 1, date: str = DATE) -> dict:
    """One Shopify order, and the day total Shopify reports for the store."""
    order = {
        "source_record_id": f"order-{store}-{date}",
        "record_type": "Order",
        "event_time": f"{date}T10:00:00+00:00",
        "store_id": store,
        "amount": "25.00",
        "currency": "USD",
        "order_id": f"1001-{store}-{date}",
        "payment_reference": f"pay-{store}-{date}",
    }
    total = {
        "source_record_id": f"SHOPEOD-{store}-{date}",
        "record_type": "DayTotal",
        "store_id": store,
        "business_date": date,
        "currency": "USD",
        "order_count": reported_orders,
        "order_total": str(25 * reported_orders),
    }
    return {"Shopify": [order, total]}


async def load(db, *feeds: dict, date: str = DATE) -> None:
    for number, feed in enumerate(feeds):
        for source, records in feed.items():
            await ingest.ingest(db, source, f"delivery-{number}-{source}", records)
    await reconciliation.reconcile(db, date)
    await db.commit()


async def day_of(db, store: str, date: str = DATE) -> StoreDay | None:
    return (
        await db.execute(
            select(StoreDay)
            .where(StoreDay.store_id == store, StoreDay.business_date == date)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


def test_clean_store_days_open_total_and_close_without_a_person():
    async def scenario(db):
        await load(db, shopify_feed("S1"), shopify_feed("S2"))

        result = await automation.run_store_day_agent(db, DATE)

        assert result == {
            "opened": 2,
            "totaled": 2,
            "closed": 2,
            "needs_review": 0,
            "failed": 0,
            "automatic_close": True,
        }
        day = await day_of(db, "S1")
        assert (day.status, day.closed_by, day.audit_version) == ("Closed", automation.AGENT, 1)
        assert day.audit_signed_by == automation.AGENT
        request = (
            await db.execute(
                select(StoreDayClosureRequest).where(StoreDayClosureRequest.store_day_id == day.id)
            )
        ).scalar_one()
        assert (request.status, request.reviewed_by) == ("Approved", automation.AGENT)
        events = (
            await db.execute(select(AuditEvent.event_type).where(AuditEvent.object_id == day.id))
        ).scalars()
        assert set(events) == {
            "STORE_DAY_OPENED",
            "STORE_DAY_RETOTALED",
            "STORE_DAY_AUDIT_SIGNED_OFF",
            "STORE_DAY_CLOSED",
        }
        # Running again finds nothing left to do.
        again = await automation.run_store_day_agent(db, DATE)
        assert (again["opened"], again["totaled"], again["closed"]) == (0, 0, 0)

    run_with_db(scenario)


def test_a_store_day_that_disagrees_with_the_source_is_left_for_a_person():
    async def scenario(db):
        # Shopify reports two orders for S2 but only one arrived.
        await load(db, shopify_feed("S1"), shopify_feed("S2", reported_orders=2))

        result = await automation.run_store_day_agent(db, DATE)

        assert (result["closed"], result["needs_review"]) == (1, 1)
        assert (await day_of(db, "S1")).status == "Closed"
        held = await day_of(db, "S2")
        assert held.status == "Auditing" and held.audit_version == 1 and held.closed_by is None
        assert held.audit_signed_by is None

        # Once the missing-data case is resolved, the next run closes the day.
        for case in (await db.execute(select(Case).where(Case.store_id == "S2"))).scalars():
            case.status = "Closed"
        await db.commit()
        result = await automation.run_store_day_agent(db)
        assert result["closed"] == 1
        assert (await day_of(db, "S2")).status == "Closed"

    run_with_db(scenario)


def test_automatic_close_can_be_turned_off_and_totals_are_still_prepared():
    async def scenario(db):
        db.add(
            SystemOption(key=AUTO_STORE_DAY_CLOSE_KEY, value="false", updated_at=datetime.now(UTC))
        )
        await load(db, shopify_feed("S1"))

        result = await automation.run_store_day_agent(db, DATE)

        assert (result["opened"], result["totaled"], result["closed"]) == (1, 1, 0)
        assert result["automatic_close"] is False
        day = await day_of(db, "S1")
        assert day.status == "Auditing" and day.audit_signed_by is None

    run_with_db(scenario)


def test_a_day_a_person_reopened_is_not_closed_again_by_the_agent():
    async def scenario(db):
        await load(db, shopify_feed("S1"))
        await automation.run_store_day_agent(db, DATE)
        await reopen_store_day("S1", DATE, ReopenRequest(rationale="Late refund"), db, FINANCE)

        result = await automation.run_store_day_agent(db, DATE)

        assert result["closed"] == 0
        assert (await day_of(db, "S1")).status == "Reopened"

    run_with_db(scenario)


def test_today_is_never_closed_and_a_day_without_data_is_not_totaled():
    async def scenario(db):
        today = datetime.now(UTC).date().isoformat()
        await load(db, shopify_feed("S1", date=today), date=today)
        db.add(StoreDay(id="empty", store_id="S9", business_date=DATE, status="Open"))
        await db.commit()

        result = await automation.run_store_day_agent(db, today)

        assert (result["opened"], result["totaled"], result["closed"]) == (1, 1, 0)
        assert (await day_of(db, "S1", today)).status == "Auditing"
        assert (await day_of(db, "S9")).audit_version == 0

    run_with_db(scenario)


def test_a_manual_retotal_still_works_and_stale_days_are_retotaled_by_the_agent():
    async def scenario(db):
        db.add(
            SystemOption(key=AUTO_STORE_DAY_CLOSE_KEY, value="false", updated_at=datetime.now(UTC))
        )
        await load(db, shopify_feed("S1"))
        await automation.run_store_day_agent(db, DATE)
        manual = await sd.retotal_store_day("S1", DATE, db, FINANCE)
        assert manual["store_day"]["audit_version"] == 2

        day = await day_of(db, "S1")
        day.totals_stale = True
        await db.commit()
        result = await automation.run_store_day_agent(db, DATE)

        assert result["totaled"] == 1
        day = await day_of(db, "S1")
        assert (day.audit_version, day.totals_stale) == (3, False)

    run_with_db(scenario)


def test_an_import_audit_settles_the_store_days_it_touched():
    async def scenario(db):
        db.add(
            SystemOption(
                key=AUTO_AUDIT_AFTER_IMPORT_KEY, value="true", updated_at=datetime.now(UTC)
            )
        )
        feed = shopify_feed("S1")
        await ingest.ingest(db, "Shopify", "shopify", feed["Shopify"])
        await db.commit()

        outcome = await auto_audit.reconcile_after_import(db, "Shopify", feed["Shopify"])

        assert outcome["runs"][0]["store_days"]["closed"] == 1
        assert (await day_of(db, "S1")).status == "Closed"

    run_with_db(scenario)


def test_closing_a_day_opens_the_next_one():
    async def scenario(db):
        await load(db, shopify_feed("S1"))
        await automation.run_store_day_agent(db, DATE)
        assert (await day_of(db, "S1")).status == "Closed"
        following = (date.fromisoformat(DATE) + timedelta(days=1)).isoformat()
        # A past day with no data stays unopened; today's day always opens.
        assert await day_of(db, "S1", following) is None
        yesterday = (datetime.now(UTC).date() - timedelta(days=1)).isoformat()
        assert await automation.open_following_day(db, "S1", yesterday) is True
        assert (await day_of(db, "S1", datetime.now(UTC).date().isoformat())).status == "Open"
        assert await automation.open_following_day(db, "S1", yesterday) is False

    run_with_db(scenario)


def test_the_attention_view_summarises_how_a_business_date_closed():
    async def scenario(db):
        await load(db, shopify_feed("S1"), shopify_feed("S2", reported_orders=2))
        await automation.run_store_day_agent(db, DATE)

        view = await sd.store_days_needing_attention(
            business_date=DATE,
            stuck_after_days=1,
            limit=200,
            db=db,
            user=FINANCE,
            late_page=1,
            late_page_size=100,
            format="json",
            queue="late",
        )

        assert view["close_summary"] == {
            "closed_automatically": 1,
            "closed_by_person": 0,
            "open": 1,
            "not_opened": 0,
        }
        assert [day["store_id"] for day in view["stuck_store_days"]] == ["S2"]
        assert view["stuck_store_days"][0]["holding_back"][0].startswith("Data: Incomplete")

    run_with_db(scenario)


def test_the_agent_configuration_lists_the_store_day_agent_and_its_close_setting():
    from apps.agents.agent_settings import defaults
    from apps.agents.routes import AGENT_ACTORS, agent_registry

    on = next(a for a in agent_registry(defaults()) if a["actor"] == automation.AGENT)
    off = next(a for a in agent_registry(defaults(), False) if a["actor"] == automation.AGENT)

    assert automation.AGENT in AGENT_ACTORS
    assert any("close" in line for line in on["can"])
    assert not any("close" in line for line in off["can"])
    assert off["limits"]["automatic close"] == "Off"


def pos_sale(rid: str, amount: str = "113.00", number: str = "1001") -> dict:
    return {
        "source_record_id": rid,
        "event_time": f"{DATE}T10:00:00+00:00",
        "record_type": "Sale",
        "amount": amount,
        "store_id": "S1",
        "register_id": "R1",
        "transaction_number": number,
        "tender_type": "Cash",
        "payment_reference": "PAY-1",
        "lines": [
            {"item_code": "SKU-1", "quantity": "1", "unit_price": amount, "tax_amount": "0.00"}
        ],
        "tenders": [{"tender_type": "Cash", "amount": amount}],
    }


def test_an_exact_duplicate_sale_is_excluded_and_its_case_closed_by_the_agent():
    async def scenario(db):
        await load(db, {"POS": [pos_sale("A"), pos_sale("B")]})
        assert (await db.execute(select(Case))).scalars().all()

        await automation.run_store_day_agent(db, DATE)

        cases = (await db.execute(select(Case))).scalars().all()
        assert [c.status for c in cases] == ["Closed"]
        from models import CanonicalTransaction

        dispositions = sorted(
            (await db.execute(select(CanonicalTransaction.disposition))).scalars().all()
        )
        assert dispositions.count("Duplicate") == 1

    run_with_db(scenario)


def test_a_duplicate_with_a_different_payment_reference_is_still_resolved_by_the_agent():
    async def scenario(db):
        other = pos_sale("B")
        other["payment_reference"] = "PAY-2"
        await load(db, {"POS": [pos_sale("A"), other]})

        await automation.run_store_day_agent(db, DATE)

        statuses = [c.status for c in (await db.execute(select(Case))).scalars().all()]
        assert statuses == ["Closed"]

    run_with_db(scenario)


def test_a_duplicate_that_differs_from_the_original_is_left_for_a_person():
    async def scenario(db):
        await load(db, {"POS": [pos_sale("A"), pos_sale("B", amount="99.00")]})

        await automation.run_store_day_agent(db, DATE)

        statuses = [c.status for c in (await db.execute(select(Case))).scalars().all()]
        assert statuses and "Closed" not in statuses

    run_with_db(scenario)
