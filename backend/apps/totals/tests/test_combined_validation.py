import asyncio

import pytest
from fastapi import HTTPException
from models import AuditTotalDefinition, Base
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.totals.routes import validate_combined_references
from apps.totals.schemas import TotalDefinitionBody


def test_combined_references_must_exist_and_have_matching_dimensions():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add_all([
                    AuditTotalDefinition(id="a", name="A", level="Store", rollups=[]),
                    AuditTotalDefinition(id="b", name="B", level="Store", rollups=[]),
                    AuditTotalDefinition(id="c", name="C", level="Register", rollups=[]),
                    AuditTotalDefinition(id="a2", name="A", revision=2, level="Store", rollups=[]),
                    AuditTotalDefinition(id="inactive", name="Inactive", level="Store", rollups=[], active=False),
                ])
                await db.commit()
                body = TotalDefinitionBody(name="Combined", calculation_basis="Combined", combined_total_ids=["a", "b"])
                await validate_combined_references(body, db, name=body.name)
                for ids, name in [(["a", "missing"], "Combined"), (["a", "c"], "Combined"),
                                  (["a", "inactive"], "Combined"),
                                  (["a", "b"], "A"), (["a", "a2"], "Combined")]:
                    body.combined_total_ids = ids
                    with pytest.raises(HTTPException) as exc:
                        await validate_combined_references(body, db, name=name)
                    assert exc.value.status_code == 422
                body.active = False
                body.combined_total_ids = ["a", "inactive"]
                await validate_combined_references(body, db, name=body.name)
        finally:
            await engine.dispose()
    asyncio.run(run())
