"""Archive-first retention guardrail."""
import asyncio
import os
import tempfile
from datetime import timedelta
from decimal import Decimal
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from models import Base, CanonicalTransaction, RetentionPolicy, RetentionRun, utcnow
from routes_operations import RetentionRunBody, execute_retention


def test_retention_archives_without_purging_until_confirmed():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(engine, expire_on_commit=False)
        with tempfile.TemporaryDirectory() as archive:
            old = utcnow() - timedelta(days=45)
            old_env = os.environ.get("NIMBUS_ARCHIVE_DIR")
            os.environ["NIMBUS_ARCHIVE_DIR"] = archive
            try:
                async with Session() as db:
                    db.add(RetentionPolicy(id="p1", dataset="canonical_transactions", retain_days=30, archive_before_purge=True, updated_by="it"))
                    db.add(CanonicalTransaction(id="tx1", business_date="2026-08-01", transaction_date=old, event_timestamp=old, processing_timestamp=old, settlement_date="2026-08-01", transaction_type="Sale", source_lineage="pos-map-v1", currency="USD", signed_amount=Decimal("10"), store_id="S1", channel_id="store", reconciliation_status="Open", disposition="Open", created_at=old))
                    await db.commit()
                    result = await execute_retention("canonical_transactions", RetentionRunBody(confirm_purge=False), db, {"user": "it.user"})
                    assert result["archived_records"] == 1 and result["purged_records"] == 0
                    assert await db.scalar(select(func.count()).select_from(CanonicalTransaction)) == 1
                    record = (await db.execute(select(RetentionRun))).scalar_one()
                    assert record.archive_location and os.path.exists(record.archive_location)
            finally:
                if old_env is None: os.environ.pop("NIMBUS_ARCHIVE_DIR", None)
                else: os.environ["NIMBUS_ARCHIVE_DIR"] = old_env
        await engine.dispose()
    asyncio.run(run())
