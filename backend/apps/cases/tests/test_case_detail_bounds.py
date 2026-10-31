import asyncio
from datetime import timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.cases.routes_lifecycle import MAX_CASE_DETAIL_ITEMS, get_case_detail, get_case_history
from models import Base, Case, Exception as DBException, utcnow


def test_case_detail_bounds_child_history_and_reports_truncation():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                now = utcnow()
                db.add(Case(
                    id="case-1", case_number="CASE-1", case_type="Test",
                    business_date="2026-10-01", store_id="S1", status="Open",
                ))
                db.add_all([
                    DBException(
                        id=f"exception-{index:03}", case_id="case-1", exception_type="Test",
                        exception_family="Test", source_system="POS", detection_origin="Test",
                        detection_rule_id="RULE-1", exception_amount=Decimal("1"),
                        estimated_exposure=Decimal("1"), severity="Low", confidence="High",
                        close_impact="No", created_at=now + timedelta(seconds=index),
                    )
                    for index in range(MAX_CASE_DETAIL_ITEMS + 1)
                ])
                await db.commit()

                detail = await get_case_detail("case-1", db, {"role": "finance", "store_scoped": False})

                assert len(detail.exceptions) == MAX_CASE_DETAIL_ITEMS
                assert detail.exceptions[0].id == f"exception-{MAX_CASE_DETAIL_ITEMS:03}"
                assert detail.truncated == {
                    "exceptions": True,
                    "findings": False,
                    "recommendations": False,
                    "decisions": False,
                }

                first_page = await get_case_history(
                    "case-1", "exceptions", db, {"role": "finance", "store_scoped": False},
                    limit=MAX_CASE_DETAIL_ITEMS,
                )
                assert first_page["next_cursor"] == first_page["items"][-1].id
                db.add(DBException(
                    id="exception-new", case_id="case-1", exception_type="Test",
                    exception_family="Test", source_system="POS", detection_origin="Test",
                    detection_rule_id="RULE-1", exception_amount=Decimal("1"),
                    estimated_exposure=Decimal("1"), severity="Low", confidence="High",
                    close_impact="No", created_at=now + timedelta(days=1),
                ))
                await db.commit()
                final_page = await get_case_history(
                    "case-1", "exceptions", db, {"role": "finance", "store_scoped": False},
                    cursor_id=first_page["next_cursor"], limit=MAX_CASE_DETAIL_ITEMS,
                )
                assert first_page["total"] == MAX_CASE_DETAIL_ITEMS + 1
                assert first_page["has_more"] is True
                assert len(first_page["items"]) == MAX_CASE_DETAIL_ITEMS
                assert final_page["total"] == MAX_CASE_DETAIL_ITEMS + 2
                assert final_page["has_more"] is False
                assert [row.id for row in final_page["items"]] == ["exception-000"]
        finally:
            await engine.dispose()

    asyncio.run(run())
