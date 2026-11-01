"""Source scheduler configuration changes must be present in the audit stream."""

import asyncio

from apps.ingest.contracts import SourceSettingPatch
from apps.ingest.models import SourceSetting
from apps.ingest.routes import update_source
from models import AuditEvent, Base
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


def test_schedule_only_source_update_is_audited():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                response = await update_source(
                    "Shopify",
                    SourceSettingPatch(
                        sync_frequency="daily",
                        sync_time="02:00",
                        sync_timezone="UTC",
                    ),
                    db,
                    {"user": "it@example.test", "role": "it"},
                )

                setting = await db.get(SourceSetting, "Shopify")
                audit_event = (
                    await db.execute(
                        select(AuditEvent).where(AuditEvent.event_type == "SOURCE_SETTING_CHANGED")
                    )
                ).scalar_one()

                assert setting.sync_frequency == "daily"
                assert setting.sync_time == "02:00"
                assert response["sync_frequency"] == "daily"
                assert audit_event.object_id == "Shopify"
                assert audit_event.actor == "it@example.test"
                assert "schedule" in audit_event.action_description.lower()
        finally:
            await engine.dispose()

    asyncio.run(run())
