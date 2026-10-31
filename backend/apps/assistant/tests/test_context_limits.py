import asyncio
from decimal import Decimal

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.assistant.chat import CASE_CONTEXT_RECORD_LIMIT, case_context
from models import Base, Case, Exception as DBException


def test_case_context_bounds_child_records_and_marks_truncation():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                case = Case(
                    id="case-1", case_number="CASE-1", case_type="Test",
                    business_date="2026-10-31", store_id="S1",
                )
                db.add(case)
                db.add_all([
                    DBException(
                        id=f"exception-{index:02}", case_id=case.id,
                        exception_type="TEST", exception_family="Test", source_system="POS",
                        detection_origin="Test", detection_rule_id="TEST",
                        exception_amount=Decimal("1"), estimated_exposure=Decimal("1"),
                        severity="Low", confidence="High", close_impact="No",
                    )
                    for index in range(CASE_CONTEXT_RECORD_LIMIT + 5)
                ])
                await db.commit()

                context = await case_context(db, case)

                assert len(context["exceptions"]) == CASE_CONTEXT_RECORD_LIMIT
                assert context["truncated"]["exceptions"] is True
                assert context["truncated"] == {
                    "exceptions": True,
                    "findings": False,
                    "recommendations": False,
                    "workflows": False,
                    "validations": False,
                }
        finally:
            await engine.dispose()

    asyncio.run(run())
