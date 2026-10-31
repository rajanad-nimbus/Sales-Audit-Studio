import asyncio

import pytest
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.corrections.routes import list_adjustments
from models import Base, TransactionAdjustment


def test_adjustment_queue_is_paged_and_reports_unfiltered_status_counts():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add_all([
                    TransactionAdjustment(
                        id=f"adjustment-{index}", adjustment_type="Amount",
                        proposed_values={"amount_delta": "1", "label": "unique-effect" if index == 4 else "common"},
                        rationale=f"review item {index}",
                        status="Proposed" if index < 3 else "Approved", proposed_by="finance",
                    )
                    for index in range(5)
                ])
                await db.commit()

                first = await list_adjustments(None, None, db, {"role": "finance"}, 1, 2)
                assert first["total"] == 5
                assert first["pages"] == 3
                assert first["status_counts"] == {"Proposed": 3, "Approved": 2, "Rejected": 0}
                assert len(first["items"]) == 2

                second = await list_adjustments(None, "Proposed", db, {"role": "finance"}, 2, 2)
                assert second["total"] == 3
                assert len(second["items"]) == 1
                assert second["status_counts"] == first["status_counts"]

                matching = await list_adjustments(None, None, db, {"role": "finance"}, 1, 50, "item 4")
                assert matching["total"] == 1
                assert matching["items"][0]["id"] == "adjustment-4"
                effect_match = await list_adjustments(None, None, db, {"role": "finance"}, 1, 50, "unique-effect")
                assert effect_match["total"] == 1
                assert effect_match["items"][0]["id"] == "adjustment-4"
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_adjustment_queue_rejects_unknown_status_filter():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                with pytest.raises(HTTPException) as exc:
                    await list_adjustments(None, "Unknown", db, {"role": "finance"})
                assert exc.value.status_code == 422
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_database_rejects_unsupported_adjustment_status():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add(TransactionAdjustment(
                    id="invalid-status", adjustment_type="Amount", proposed_values={"amount_delta": "1"},
                    status="Pending", rationale="invalid state", proposed_by="test",
                ))
                with pytest.raises(IntegrityError):
                    await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(run())
