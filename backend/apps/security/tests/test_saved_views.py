"""Saved views: a person who has never saved one still gets an answer, not a server error."""

import asyncio

from models import Base
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.security.routes import SavedViewsEnvelope, get_my_saved_views, update_my_saved_views

USER = {"user": "new.person@example.test", "ontology_user_id": "u-1", "role": "finance"}


def test_a_first_time_user_gets_an_empty_list_and_then_their_saved_views():
    async def go():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                assert await get_my_saved_views("store_days", db, USER) == {"views": []}

                envelope = SavedViewsEnvelope(
                    views=[{"name": "Late stores", "params": {"status": "Auditing"}}]
                )
                await update_my_saved_views("store_days", envelope, db, USER)
                saved = await get_my_saved_views("store_days", db, USER)
                assert [view["name"] for view in saved["views"]] == ["Late stores"]

                # Views are kept per screen and per person.
                assert await get_my_saved_views("cases", db, USER) == {"views": []}
                other = {**USER, "user": "someone.else@example.test", "ontology_user_id": "u-2"}
                assert await get_my_saved_views("store_days", db, other) == {"views": []}
        finally:
            await engine.dispose()

    asyncio.run(go())
