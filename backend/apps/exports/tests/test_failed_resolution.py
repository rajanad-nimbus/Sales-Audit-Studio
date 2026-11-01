import asyncio

import pytest
from fastapi import HTTPException
from models import AuditEvent, Base, ExportBatch, ExportDestination, ExportItem
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.exports.routes import FailedExportResolution, cancel_batch, reconcile_failed_batch


@pytest.mark.parametrize("outcome", ["received", "not_received"])
def test_failed_export_resolution_requires_evidence_and_preserves_delivery_history(outcome):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add(ExportDestination(id="d", name="ERP", kind="webhook", format="json", config={}))
                db.add(ExportBatch(id="b", destination_id="d", dataset="resolutions", status="Failed",
                                   idempotency_key="original", created_by="it", payload="original payload"))
                db.add(ExportItem(id="i", batch_id="b", destination_id="d", dataset="resolutions",
                                  object_id="case", object_version="v1", state="Pending"))
                await db.commit()
                with pytest.raises(HTTPException, match="provide evidence"):
                    await cancel_batch("b", db, {"user": "it"})
                assert (await db.get(ExportItem, "i")).state == "Pending"
                result = await reconcile_failed_batch("b", FailedExportResolution(outcome=outcome, evidence="Receiver ticket 123"),
                                                      db, {"user": "it"})
                batch = await db.get(ExportBatch, "b")
                event = (await db.execute(select(AuditEvent))).scalar_one()
                assert event.after_state["evidence"] == "Receiver ticket 123"
                assert event.after_state["original_idempotency_key"] == "original"
                assert batch.payload == "original payload"
                if outcome == "received":
                    assert result["status"] == "Acknowledged" and batch.idempotency_key == "original"
                    assert (await db.get(ExportItem, "i")).state == "Delivered"
                else:
                    assert result["status"] == "Cancelled" and batch.idempotency_key == "original"
                    assert await db.get(ExportItem, "i") is None
                with pytest.raises(HTTPException) as error:
                    await reconcile_failed_batch("b", FailedExportResolution(outcome=outcome, evidence="again"), db, {"user": "it"})
                assert error.value.status_code == 409
        finally:
            await engine.dispose()
    asyncio.run(run())
