"""Malformed identifiers are quarantined without aborting their delivery."""

import asyncio

import pytest
from apps.ingest import service
from models import Base, CanonicalTransaction, SourceRecord
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_record_id", ["not", "a", "string"]),
        ("source_record_id", "x" * 256),
        ("source_record_id", " padded-id"),
        ("store_id", {"not": "a string"}),
        ("store_id", "S" * 51),
        ("store_id", "S1 "),
        ("payment_reference", ["not", "a", "string"]),
        ("settlement_reference", "R" * 256),
    ],
)
def test_invalid_identifier_is_quarantined_and_next_record_is_ingested(field, value):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                base = {
                    "source_record_id": "bad",
                    "event_time": "2026-10-01T10:00:00Z",
                    "record_type": "Sale",
                    "amount": "1.00",
                    "store_id": "S1",
                }
                invalid = {**base, field: value}
                valid = {**base, "source_record_id": "good"}

                stats = await service.ingest(db, "POS", "delivery", [invalid, valid])

                records = (
                    (await db.execute(select(SourceRecord).order_by(SourceRecord.status)))
                    .scalars()
                    .all()
                )
                transactions = (await db.execute(select(CanonicalTransaction))).scalars().all()
                assert stats["quarantined"] == 1
                assert stats["canonical_created"] == 1
                assert len(records) == 2 and len(transactions) == 1
                assert sorted(record.status for record in records) == ["Processed", "Quarantined"]
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_parser_rejects_each_string_field_over_its_database_limit():
    base = {
        "source_record_id": "1",
        "event_time": "2026-10-01T10:00:00Z",
        "record_type": "Sale",
        "amount": "1",
        "store_id": "S1",
    }
    for field, max_length in service.STRING_FIELD_LIMITS.items():
        record = {**base, field: "x" * (max_length + 1)}
        with pytest.raises(ValueError, match=field):
            service.parse("POS", record)
