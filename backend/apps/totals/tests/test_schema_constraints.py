import asyncio

import pytest
from models import AuditTotalDefinition, Base
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def test_total_definition_name_and_revision_are_unique_in_model_schema():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add_all(
                    [
                        AuditTotalDefinition(id="first", name="Retail sales", revision=1),
                        AuditTotalDefinition(id="duplicate", name="Retail sales", revision=1),
                    ]
                )
                with pytest.raises(IntegrityError):
                    await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(run())
