import asyncio

import pytest
from fastapi import HTTPException
from models import AuditEvent, AuditTotalDefinition, Base, StoreDay
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.totals.routes import create_definition, update_definition
from apps.totals.schemas import TotalDefinitionBody


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


def test_failed_revision_commit_rolls_back_day_invalidation_and_audit(monkeypatch):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add(
                    StoreDay(
                        id="day-1",
                        store_id="S1",
                        business_date="2026-10-01",
                        audit_version=1,
                        totals_stale=False,
                    )
                )
                await db.commit()

                async def fail_after_flush():
                    await db.flush()
                    raise IntegrityError(
                        "insert",
                        {},
                        Exception(
                            "UNIQUE constraint failed: "
                            "audit_total_definitions.name, audit_total_definitions.revision"
                        ),
                    )

                monkeypatch.setattr(db, "commit", fail_after_flush)
                with pytest.raises(HTTPException, match="revision already exists"):
                    await create_definition(
                        TotalDefinitionBody(name="POS Net"), db, {"user": "finance.user"}
                    )

                assert await db.scalar(
                    select(StoreDay.totals_stale).where(StoreDay.id == "day-1")
                ) is False
                assert await db.scalar(
                    select(func.count()).select_from(AuditTotalDefinition)
                ) == 0
                assert await db.scalar(select(func.count()).select_from(AuditEvent)) == 0
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_stale_revision_update_cannot_overwrite_newer_definition():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                user = {"user": "finance.user"}
                original = await create_definition(
                    TotalDefinitionBody(name="POS Net", description="initial"), db, user
                )
                latest = await update_definition(
                    original["id"], TotalDefinitionBody(name="Ignored", description="latest"), db, user
                )

                with pytest.raises(HTTPException) as error:
                    await update_definition(
                        original["id"],
                        TotalDefinitionBody(name="Ignored", description="stale edit"),
                        db,
                        user,
                    )

                assert error.value.status_code == 409
                assert "stale" in error.value.detail
                rows = (
                    await db.execute(
                        select(AuditTotalDefinition)
                        .where(AuditTotalDefinition.name == "POS Net")
                        .order_by(AuditTotalDefinition.revision)
                    )
                ).scalars().all()
                assert len(rows) == 2
                assert rows[-1].id == latest["id"]
                assert rows[-1].description == "latest"
        finally:
            await engine.dispose()

    asyncio.run(run())
