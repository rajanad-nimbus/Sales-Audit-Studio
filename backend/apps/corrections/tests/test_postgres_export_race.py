"""Verify correction/export serialization with real PostgreSQL row locks."""
import asyncio
import os
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.corrections.routes import approve_adjustment
from apps.exports import routes as exports
from apps.ingest import reconciliation, service
from models import Base, CanonicalTransaction, Case, ExportDestination, TransactionAdjustment

POSTGRES_URL = os.getenv("ZETSA_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="ZETSA_TEST_POSTGRES_URL not configured")


def test_export_skips_locked_case_and_correction_waits_for_reserved_export(monkeypatch):
    async def run():
        schema = "zetsa_test_" + uuid.uuid4().hex
        admin = create_async_engine(POSTGRES_URL)
        engine = None
        try:
            async with admin.begin() as connection:
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            engine = create_async_engine(POSTGRES_URL, connect_args={"server_settings": {"search_path": schema}})
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with sessions() as db:
                common = {"event_time": "2026-10-01T10:00:00Z", "store_id": "S1", "payment_reference": "REF"}
                await service.ingest(db, "POS", "pos", [{**common, "source_record_id": "pos", "record_type": "Sale", "amount": "100"}])
                await service.ingest(db, "Processor", "processor", [{**common, "source_record_id": "capture", "record_type": "Capture", "amount": "90"}])
                await reconciliation.reconcile(db, "2026-10-01")
                case = (await db.execute(select(Case))).scalar_one()
                case.status = "Closed"
                case_id = case.id
                transaction = (await db.execute(select(CanonicalTransaction).where(CanonicalTransaction.transaction_type == "Capture"))).scalar_one()
                db.add(TransactionAdjustment(id="correction", transaction_id=transaction.id, adjustment_type="Amount",
                    proposed_values={"amount_delta": "10"}, status="Proposed", rationale="verified", proposed_by="analyst"))
                db.add(ExportDestination(id="dest", name="ERP", kind="file", format="json", config={}))
                await db.commit()

            async def fail_delivery(_dest, batch):
                batch.status = "Failed"

            monkeypatch.setattr(exports, "deliver", fail_delivery)
            async with sessions() as locked, sessions() as exporter:
                await locked.execute(select(Case).where(Case.id == case_id).with_for_update())
                result = await asyncio.wait_for(exports.run_export(exports.RunIn(destination_id="dest", dataset="resolutions"),
                                                                   exporter, {"user": "it"}), timeout=5)
                assert result["count"] == 0
                await exporter.rollback()
                await locked.rollback()
                result = await exports.run_export(exports.RunIn(destination_id="dest", dataset="resolutions"),
                                                  exporter, {"user": "it"})
                assert result["status"] == "Failed"
            async with sessions() as db:
                with pytest.raises(HTTPException, match="pending export"):
                    await approve_adjustment("correction", db, {"user": "finance"})
                assert (await db.get(TransactionAdjustment, "correction")).status == "Proposed"
        finally:
            if engine:
                await engine.dispose()
            async with admin.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await admin.dispose()
    asyncio.run(run())
