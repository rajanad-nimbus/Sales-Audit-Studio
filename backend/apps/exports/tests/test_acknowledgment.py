import asyncio

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.exports.routes import AckIn, acknowledge
from models import AuditEvent, Base, ExportBatch, ExportDestination


def test_acknowledgment_is_a_single_atomic_state_transition():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add(ExportDestination(id="destination", name="ERP", kind="file", format="json", config={}))
                db.add(ExportBatch(id="batch", destination_id="destination", dataset="resolutions",
                                   status="Sent", idempotency_key="key", created_by="it"))
                await db.commit()

                result = await acknowledge("batch", AckIn(reference="receiver-123"), db, {"user": "it"})
                assert result["status"] == "Acknowledged"
                assert result["response_ref"] == "receiver-123"

                with pytest.raises(HTTPException) as error:
                    await acknowledge("batch", AckIn(reference="duplicate"), db, {"user": "it"})
                assert error.value.status_code == 409
                events = (await db.execute(select(AuditEvent).where(
                    AuditEvent.event_type == "EXPORT_ACKNOWLEDGED"
                ))).scalars().all()
                assert len(events) == 1
        finally:
            await engine.dispose()

    asyncio.run(run())
