"""Delivery history groups statuses and picks the newest quarantine reason."""

import asyncio
from datetime import UTC, datetime, timedelta

from apps.ingest.routes import delivery_history
from models import Base, SourceRecord
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


def test_delivery_history_groups_records_and_orders_recent_deliveries():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                now = datetime.now(UTC)
                db.add_all(
                    [
                        _source_record("p1", "POS", "batch-1", now, "Processed"),
                        _source_record(
                            "p2", "POS", "batch-1", now + timedelta(seconds=1), "Quarantined", "old"
                        ),
                        _source_record(
                            "p3", "POS", "batch-1", now + timedelta(seconds=2), "Quarantined", "new"
                        ),
                        _source_record(
                            "b1", "Bank", "batch-2", now + timedelta(seconds=3), "Archived"
                        ),
                    ]
                )
                await db.commit()

                result = await delivery_history(limit=25, db=db, _={})

                assert [item["delivery_id"] for item in result] == ["batch-2", "batch-1"]
                assert result[0]["source_system"] == "Bank"
                assert (result[0]["records"], result[0]["processed"], result[0]["quarantined"]) == (
                    1,
                    0,
                    0,
                )
                assert result[1]["source_system"] == "POS"
                assert (result[1]["records"], result[1]["processed"], result[1]["quarantined"]) == (
                    3,
                    1,
                    2,
                )
                assert result[1]["last_error"] == "new"
        finally:
            await engine.dispose()

    asyncio.run(run())


def _source_record(
    identifier: str,
    source_system: str,
    delivery_id: str,
    received_at: datetime,
    status: str,
    reason: str | None = None,
) -> SourceRecord:
    return SourceRecord(
        id=identifier,
        source_system=source_system,
        source_version="1",
        source_record_id=identifier,
        delivery_id=delivery_id,
        payload=b"{}",
        payload_hash="0" * 64,
        received_at=received_at,
        source_event_time=received_at,
        status=status,
        quarantine_reason=reason,
    )
