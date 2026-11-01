import asyncio
from datetime import timedelta

from apps.cases.routes_lifecycle import list_cases_by_status
from fastapi import Response
from models import Base, Case, utcnow
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


def test_status_case_list_has_stable_pages_and_total_headers():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                now = utcnow()
                db.add_all([
                    Case(
                        id=f"case-{index}", case_number=f"CASE-{index}", case_type="Test",
                        business_date="2026-10-01", store_id="S1", status="Open",
                        updated_at=now - timedelta(minutes=index),
                    )
                    for index in range(3)
                ])
                await db.commit()

                first_response = Response()
                first = await list_cases_by_status(
                    "Open", db, {"role": "finance", "store_scoped": False},
                    skip=0, limit=2, response=first_response,
                )
                second_response = Response()
                second = await list_cases_by_status(
                    "Open", db, {"role": "finance", "store_scoped": False},
                    skip=2, limit=2, response=second_response,
                )

                assert [case.id for case in first] == ["case-0", "case-1"]
                assert [case.id for case in second] == ["case-2"]
                assert first_response.headers["X-Total-Count"] == second_response.headers["X-Total-Count"] == "3"
                assert first_response.headers["X-Has-More"] == "true"
                assert second_response.headers["X-Has-More"] == "false"
        finally:
            await engine.dispose()

    asyncio.run(run())
