import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.totals.routes import create_definition, update_definition
from apps.totals.schemas import TotalDefinitionBody
from models import AuditEvent, Base


def test_total_definition_revisions_are_audited_without_store_days():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                user = {"user": "finance.user"}
                created = await create_definition(TotalDefinitionBody(name="POS Net"), db, user)
                revised = await update_definition(
                    created["id"], TotalDefinitionBody(name="Ignored"), db, user
                )

                events = (
                    await db.execute(
                        select(AuditEvent).where(
                            AuditEvent.object_type == "AuditTotalDefinition"
                        )
                    )
                ).scalars().all()

                assert created["revision"] == 1
                assert revised["revision"] == 2
                assert len(events) == 2
                creation = next(event for event in events if event.event_type == "TOTAL_DEFINITION_CREATED")
                revision = next(event for event in events if event.event_type == "TOTAL_DEFINITION_REVISED")
                assert creation.object_id == created["id"]
                assert creation.actor == "finance.user"
                assert len(creation.after_state["configuration_hash"]) == 64
                assert revision.object_id == revised["id"]
                assert revision.actor == "finance.user"
                assert revision.after_state["previous_definition_id"] == created["id"]
                assert revision.after_state["previous_revision"] == 1
                assert revision.after_state["revision"] == 2
                assert len(revision.after_state["configuration_hash"]) == 64
        finally:
            await engine.dispose()

    asyncio.run(run())
