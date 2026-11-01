import asyncio

import pytest
from models import Base, ReconciliationMatch
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


def test_manual_match_status_is_limited_to_review_state_machine():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                for index, status in enumerate(("Proposed", "Approved", "Rejected")):
                    db.add(ReconciliationMatch(
                        id=f"valid-{index}", transaction_ids=["a", "b"], match_type="Manual",
                        status=status, rationale="reviewed", proposed_by="finance",
                    ))
                await db.commit()
                db.add(ReconciliationMatch(
                    id="invalid", transaction_ids=["a", "b"], match_type="Manual",
                    status="Delivered", rationale="invalid state", proposed_by="finance",
                ))
                with pytest.raises(IntegrityError):
                    await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(run())
