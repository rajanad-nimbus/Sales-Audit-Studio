import asyncio

from apps.cases.models import Case
from apps.development.routes import clear_scale_demo
from models import Base
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


def test_scale_demo_cleanup_deletes_only_reserved_synthetic_case_numbers():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add_all(
                    [
                        Case(
                            id="synthetic",
                            case_number="ZS-DEMO-A1B2-0000001",
                            case_type="test",
                            business_date="2026-10-01",
                            store_id="S1",
                        ),
                        Case(
                            id="business",
                            case_number="ZS-A1B2-0000001",
                            case_type="test",
                            business_date="2026-10-01",
                            store_id="S1",
                        ),
                    ]
                )
                await db.commit()

                result = await clear_scale_demo(db, {"role": "it"})

                remaining = (
                    (await db.execute(select(Case.case_number).order_by(Case.case_number)))
                    .scalars()
                    .all()
                )
                assert result == {"deleted": 1}
                assert remaining == ["ZS-A1B2-0000001"]
        finally:
            await engine.dispose()

    asyncio.run(run())
