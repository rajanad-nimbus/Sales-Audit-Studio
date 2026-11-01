import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from apps.agents import action_center
from apps.assistant import chat
from fastapi import HTTPException
from main import update_case
from models import Base, Case
from schemas import CaseUpdate
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


def test_summary_groups_and_bulk_respect_store_and_currency():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add_all([Case(id="usd", case_number="USD", case_type="test", store_id="S1",
                                 business_date="2026-10-01", currency="USD", total_exposure=Decimal("10")),
                            Case(id="eur", case_number="EUR", case_type="test", store_id="S1",
                                 business_date="2026-10-01", currency="EUR", total_exposure=Decimal("20")),
                            Case(id="other", case_number="OTHER", case_type="test", store_id="S2",
                                 business_date="2026-10-01", currency="USD", total_exposure=Decimal("1000")),
                            Case(id="stale", case_number="STALE", case_type="test", store_id="S1",
                                 business_date="2026-10-01", currency="USD", total_exposure=Decimal("500"), amount_needs_review=True)])
                await db.commit()
                user = {"user": "finance", "role": "finance", "store_scoped": True, "stores": ["S1"]}
                summary_selects = 0

                def count_summary_selects(_connection, _cursor, statement, _parameters, _context, _executemany):
                    nonlocal summary_selects
                    if statement.lstrip().upper().startswith("SELECT"):
                        summary_selects += 1

                event.listen(engine.sync_engine, "before_cursor_execute", count_summary_selects)
                try:
                    summary = await action_center.summary(db, user)
                finally:
                    event.remove(engine.sync_engine, "before_cursor_execute", count_summary_selects)
                assert summary_selects == 4
                literal_wildcard = await action_center.list_cases(
                    action_center.Filters(q="%"), "sla", "asc", 1, 50, db, user
                )
                assert literal_wildcard["total"] == 0
                assert summary["open_total"] == 3 and summary["open_exposure"] is None and summary["stale_amount_cases"] == 1
                assert {row["currency"]: row["exposure"] for row in summary["amounts_by_currency"]} == {
                    "USD": "10.00", "EUR": "20.00"}
                groups = await action_center.groups("case_type", action_center.Filters(), db, user)
                assert groups[0]["count"] == 3 and groups[0]["exposure"] is None and groups[0]["stale_amount_cases"] == 1
                assert len(groups[0]["amounts_by_currency"]) == 2
                context = await chat.build_context(db, "other", "OTHER", user)
                assert "selected_case" not in context and "mentioned_cases" not in context
                assert {item["currency"] for item in context["open_amounts_by_currency"]} == {"USD", "EUR"}
                answer = chat.rule_answer("summary", context, "finance")["answer"]
                assert "USD" in answer and "EUR" in answer and "1000" not in answer
                with pytest.raises(HTTPException) as exc:
                    await action_center.bulk(action_center.BulkRequest(action="assign", ids=["usd", "other"]), db, user)
                assert exc.value.status_code == 403
                assert (await db.get(Case, "usd")).assigned_to is None
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_finance_can_set_unknown_case_currency_once():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add(Case(id="case", case_number="CASE", case_type="test", store_id="S1", business_date="2026-10-01"))
                await db.commit()
                user = {"user": "finance", "role": "finance", "store_scoped": True, "stores": ["S1"]}
                with pytest.raises(HTTPException) as exc:
                    await update_case("case", CaseUpdate(currency="eur"), db, user)
                assert exc.value.status_code == 422
                updated = await update_case("case", CaseUpdate(currency="EUR"), db, user)
                assert updated.currency == "EUR"
                with pytest.raises(HTTPException) as exc:
                    await update_case("case", CaseUpdate(currency="USD"), db, user)
                assert exc.value.status_code == 409
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_assistant_counts_all_decisions_and_checks_currency_beyond_context_limit():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add_all([
                    Case(
                        id=f"case-{index}", case_number=f"CASE-{index}", case_type="Test",
                        business_date="2026-10-01", store_id="S1", status="In Review",
                        currency="USD" if index < 8 else "EUR" if index < 10 else "CAD",
                        total_exposure=Decimal(100 - index),
                        snoozed_until=(datetime.now(UTC) + timedelta(days=1)
                                       if index in (8, 10) else None),
                    )
                    for index in range(11)
                ])
                await db.commit()

                user = {"user": "finance", "role": "finance"}
                context = await chat.build_context(db, None, user=user)
                decision_answer = chat.rule_answer("What needs my decision?", context)["answer"]
                largest_answer = chat.rule_answer("What is the biggest exposure?", context)["answer"]

                assert context["needs_decision_count"] == 9
                assert len(context["needs_decision"]) == 8
                assert set(context["needs_decision_currencies"]) == {"USD", "EUR"}
                assert "9 case(s) are waiting" in decision_answer
                assert "cannot be ranked across different or unknown currencies" in largest_answer
        finally:
            await engine.dispose()

    asyncio.run(run())
