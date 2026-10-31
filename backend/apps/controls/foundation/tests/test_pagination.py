"""Search and pagination behavior for the Foundation Data endpoint."""
import asyncio

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.controls.foundation.routes import list_references
from models import Base, FoundationReference


def test_foundation_reference_search_returns_bounded_page_and_global_counts():
    async def go():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            Session = async_sessionmaker(engine, expire_on_commit=False)
            async with Session() as db:
                db.add_all([
                    FoundationReference(id="item-a", category="Item", code="SKU-100", name="Blue shirt",
                                        active=True, attributes={}),
                    FoundationReference(id="item-b", category="Item", code="SKU-200", name="Red shirt",
                                        active=True, attributes={}),
                    FoundationReference(id="item-c", category="Item", code="SKU-300", name="Blue coat",
                                        active=False, attributes={}),
                    FoundationReference(id="store", category="Store", code="S1", name="Blue shop",
                                        active=True, attributes={}),
                ])
                await db.commit()

                page = await list_references(
                    category=None, categories="Item,Store", q="blue", limit=1, offset=1,
                    db=db, _={"role": "finance"},
                )

                assert page["total"] == 3
                assert len(page["items"]) == 1
                assert page["items"][0]["code"] == "SKU-300"
                assert page["category_counts"] == {"Item": 3, "Store": 1}
        finally:
            await engine.dispose()
    asyncio.run(go())


def test_foundation_reference_rejects_unknown_category():
    async def go():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            Session = async_sessionmaker(engine, expire_on_commit=False)
            async with Session() as db:
                try:
                    await list_references(
                        category=None, categories="Unknown", q=None, limit=200, offset=0,
                        db=db, _={"role": "finance"},
                    )
                except HTTPException as exc:
                    assert exc.status_code == 422
                else:
                    raise AssertionError("Unknown categories must be rejected")
        finally:
            await engine.dispose()
    asyncio.run(go())
