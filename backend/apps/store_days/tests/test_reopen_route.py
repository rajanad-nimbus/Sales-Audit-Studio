"""The reopen endpoint: reason required, unknown case, not agent-resolved, and the success path."""

import asyncio
from decimal import Decimal

import pytest
from apps.audit.models import AuditEvent
from apps.cases.routes_lifecycle import reopen_case
from apps.store_days.duplicates import resolve_duplicate_cases
from apps.store_days.tests.test_duplicate_resolution import DAY, _order
from fastapi import HTTPException
from models import Base, CanonicalTransaction, Case
from models import Exception as DBException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

FINANCE = {"user": "finance@example.test", "role": "finance"}


def seeded(test):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                for record, tx in (_order("first", 0, "ORD-1"), _order("second", 5, "ORD-1")):
                    db.add_all([record, tx])
                db.add(
                    Case(id="c", case_number="C-1", case_type="Duplicate Sales",
                         business_date=DAY, store_id="S1")
                )
                db.add(
                    DBException(
                        id="e", case_id="c", exception_type="SHOPIFY_DUPLICATE_ORDER",
                        exception_family="Duplicate", source_system="Shopify",
                        detection_origin="rule", detection_rule_id="r",
                        exception_amount=Decimal("20"), estimated_exposure=Decimal("20"),
                        severity="Low", confidence="High", close_impact="None",
                    )
                )
                db.add(
                    AuditEvent(
                        id="a", seq=1, case_id="c", event_type="CASE_CREATED", actor="x",
                        object_type="Case", object_id="c", action_description="d",
                        after_state={"transaction_ids": ["tx-second"]},
                    )
                )
                await db.commit()
                return await test(db)
        finally:
            await engine.dispose()

    return asyncio.run(run())


def test_a_short_reason_is_refused():
    async def test(db):
        with pytest.raises(HTTPException) as refused:
            await reopen_case("c", {"reason": "too short"}, db, FINANCE)
        assert refused.value.status_code == 422

    seeded(test)


def test_an_unknown_case_is_not_found():
    async def test(db):
        with pytest.raises(HTTPException) as refused:
            await reopen_case("nope", {"reason": "a long enough reason"}, db, FINANCE)
        assert refused.value.status_code == 404

    seeded(test)


def test_a_case_the_agent_did_not_resolve_cannot_be_reopened():
    async def test(db):
        with pytest.raises(HTTPException) as refused:
            await reopen_case("c", {"reason": "a long enough reason"}, db, FINANCE)
        assert refused.value.status_code == 409

    seeded(test)


def test_reopening_an_agent_resolution_restores_the_sale_and_records_the_event():
    async def test(db):
        assert await resolve_duplicate_cases(db, "S1", DAY, "agent") == 1
        await db.commit()
        result = await reopen_case("c", {"reason": "the copy was a real second order"}, db, FINANCE)
        assert result["case_id"] == "c" and result["status"] != "Auto-Resolved"
        assert (await db.get(CanonicalTransaction, "tx-second")).disposition != "Duplicate"
        events = (await db.execute(
            AuditEvent.__table__.select().where(AuditEvent.event_type == "CASE_REOPENED")
        )).all()
        assert len(events) == 1
        # Reopening twice is refused: the case is no longer agent-resolved.
        with pytest.raises(HTTPException) as refused:
            await reopen_case("c", {"reason": "the copy was a real second order"}, db, FINANCE)
        assert refused.value.status_code == 409

    seeded(test)
