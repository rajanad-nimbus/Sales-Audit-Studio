import asyncio
from datetime import timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.operations.batch_routes import status
from models import Base, BatchRun, utcnow


def test_batch_status_does_not_expose_run_errors_or_source_counts():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with sessions() as db:
                now = utcnow()
                db.add_all(
                    [
                        BatchRun(
                            id="successful-run",
                            business_date="2026-10-01",
                            status="Succeeded",
                            started_at=now - timedelta(hours=1),
                            finished_at=now - timedelta(minutes=55),
                            transactions_loaded=120,
                            matched_pairs=42,
                            cases_created=8,
                            source_counts={"POS": {"canonical_created": 120}},
                        ),
                        BatchRun(
                            id="failed-run",
                            business_date="2026-10-02",
                            status="Failed",
                            started_at=now - timedelta(minutes=10),
                            finished_at=now - timedelta(minutes=9),
                            error="database connection failed: password=hunter2",
                            source_counts={"POS": {"private_count": 3}},
                        ),
                    ]
                )
                await db.commit()

                result = await status(db)

                assert result["last_failed"] is True
                assert result["last_success"]["transactions_loaded"] == 120
                assert result["last_success"]["cases_created"] == 8
                assert result["last_run"]["status"] == "Failed"
                assert "error" not in result["last_run"]
                assert "source_counts" not in result["last_run"]
                assert "error" not in result["last_success"]
                assert "source_counts" not in result["last_success"]
                assert "hunter2" not in str(result)
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_batch_status_reports_first_run_failure_without_prior_success():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with sessions() as db:
                db.add(
                    BatchRun(
                        id="first-run",
                        business_date="2026-10-01",
                        status="Failed",
                        error="source unavailable",
                    )
                )
                await db.commit()

                result = await status(db)

                assert result["freshness"] == "never"
                assert result["last_success"] is None
                assert result["last_failed"] is True
                assert result["last_run"]["status"] == "Failed"
                assert "error" not in result["last_run"]
        finally:
            await engine.dispose()

    asyncio.run(run())
