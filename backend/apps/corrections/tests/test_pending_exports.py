import asyncio

import pytest
from fastapi import HTTPException
from models import (
    Base,
    CanonicalTransaction,
    Case,
    ExportBatch,
    ExportDestination,
    ExportItem,
    TransactionAdjustment,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.corrections.routes import approve_adjustment
from apps.exports.routes import FailedExportResolution, cancel_batch
from apps.ingest import reconciliation, service


@pytest.mark.parametrize(
    "batch_status,item_state",
    [("Pending", "Pending"), ("Failed", "Pending"), ("Sent", "Delivered")],
)
def test_correction_waits_for_pending_export_but_allows_delivered_history(batch_status, item_state):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                common = {
                    "event_time": "2026-10-01T10:00:00Z",
                    "store_id": "S1",
                    "payment_reference": "REF",
                }
                await service.ingest(
                    db,
                    "POS",
                    "pos",
                    [{**common, "source_record_id": "pos", "record_type": "Sale", "amount": "100"}],
                )
                await service.ingest(
                    db,
                    "Processor",
                    "processor",
                    [
                        {
                            **common,
                            "source_record_id": "capture",
                            "record_type": "Capture",
                            "amount": "90",
                        }
                    ],
                )
                await reconciliation.reconcile(db, "2026-10-01")
                case = (await db.execute(select(Case))).scalar_one()
                case.status = "Closed"
                case_id = case.id
                transaction = (
                    await db.execute(
                        select(CanonicalTransaction).where(
                            CanonicalTransaction.transaction_type == "Capture"
                        )
                    )
                ).scalar_one()
                db.add(
                    TransactionAdjustment(
                        id="correction",
                        transaction_id=transaction.id,
                        adjustment_type="Amount",
                        proposed_values={"amount_delta": "10"},
                        status="Proposed",
                        rationale="verified",
                        proposed_by="analyst",
                    )
                )
                db.add(
                    ExportDestination(id="dest", name="ERP", kind="file", format="json", config={})
                )
                db.add(
                    ExportBatch(
                        id="batch",
                        destination_id="dest",
                        dataset="resolutions",
                        status=batch_status,
                        idempotency_key="key",
                        created_by="it",
                    )
                )
                db.add(
                    ExportItem(
                        id="item",
                        batch_id="batch",
                        destination_id="dest",
                        dataset="resolutions",
                        object_id=case.id,
                        object_version="v1",
                        state=item_state,
                    )
                )
                await db.commit()
                if item_state == "Pending":
                    with pytest.raises(HTTPException, match="pending export") as error:
                        await approve_adjustment("correction", db, {"user": "finance"})
                    assert error.value.status_code == 409
                    assert (await db.get(TransactionAdjustment, "correction")).status == "Proposed"
                    await db.rollback()
                    batch = await db.get(ExportBatch, "batch")
                    batch.status = "Failed"
                    await db.commit()
                    await cancel_batch(
                        "batch",
                        db,
                        {"user": "it"},
                        FailedExportResolution(
                            outcome="not_received", evidence="Receiver confirmed no delivery"
                        ),
                    )
                result = await approve_adjustment("correction", db, {"user": "finance"})
                assert result["status"] == "Approved"
                assert (await db.get(Case, case_id)).amount_needs_review
        finally:
            await engine.dispose()

    asyncio.run(run())
