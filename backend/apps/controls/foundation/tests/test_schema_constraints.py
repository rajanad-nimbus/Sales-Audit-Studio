"""Database invariants for retail foundation references."""

import asyncio

import pytest
from models import Base, FoundationReference
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.mark.parametrize("field", ["category", "code", "name"])
@pytest.mark.parametrize("value", ["", "   "])
def test_database_rejects_blank_required_reference_text(field, value):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                values = {"category": "Store", "code": "S1", "name": "Store 1"}
                values[field] = value
                db.add(FoundationReference(id="ref", attributes={}, active=True, **values))

                with pytest.raises(IntegrityError):
                    await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(run())
