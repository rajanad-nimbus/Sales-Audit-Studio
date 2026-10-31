"""Archive-first retention guardrail."""
import asyncio
import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from models import (AuditEvent, Base, CanonicalTransaction, RetentionPolicy, RetentionRun, SourceRecord,
                    TransactionLine, utcnow)
from apps.operations.routes import (RetentionBody, RetentionRunBody, execute_retention, preview_retention,
                                    serialise_row, set_retention)


def test_archive_serialization_preserves_scalar_types():
    instant = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    assert serialise_row({
        "count": 4,
        "enabled": False,
        "amount": Decimal("12.30"),
        "created_at": instant,
        "payload": b"data",
        "missing": None,
    }) == {
        "count": 4,
        "enabled": False,
        "amount": "12.30",
        "created_at": instant.isoformat(),
        "payload": "ZGF0YQ==",
        "missing": None,
    }


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
                    archived = open(record.archive_location, "rb").read()
                    assert hashlib.sha256(archived).hexdigest() == record.archive_hash
                    assert json.loads(archived.splitlines()[0])["id"] == "tx1"
            finally:
                if old_env is None: os.environ.pop("NIMBUS_ARCHIVE_DIR", None)
                else: os.environ["NIMBUS_ARCHIVE_DIR"] = old_env
        await engine.dispose()
    asyncio.run(run())


def test_retention_processes_a_deterministic_bounded_batch(monkeypatch):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr("apps.operations.routes.MAX_RETENTION_BATCH", 1)
        try:
            async with Session() as db:
                old = utcnow() - timedelta(days=45)
                db.add(RetentionPolicy(
                    id="policy", dataset="canonical_transactions", retain_days=30,
                    archive_before_purge=False, updated_by="it",
                ))
                for identifier in ("tx-b", "tx-a"):
                    db.add(CanonicalTransaction(
                        id=identifier, business_date="2026-08-01", transaction_date=old,
                        event_timestamp=old, processing_timestamp=old, settlement_date="2026-08-01",
                        transaction_type="Sale", source_lineage="pos-map-v1", currency="USD",
                        signed_amount=Decimal("10"), store_id="S1", channel_id="store",
                        reconciliation_status="Open", disposition="Open", created_at=old,
                    ))
                await db.commit()
                result = await execute_retention(
                    "canonical_transactions", RetentionRunBody(confirm_purge=True), db, {"user": "it.user"}
                )
                assert result["eligible_records"] == result["purged_records"] == 1
                assert result["more_eligible"] is True
                remaining = (await db.execute(select(CanonicalTransaction.id))).scalars().all()
                assert remaining == ["tx-b"]
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_retention_archive_byte_limit_purges_only_archived_records(tmp_path, monkeypatch):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(engine, expire_on_commit=False)
        old = utcnow() - timedelta(days=45)
        monkeypatch.setenv("NIMBUS_ARCHIVE_DIR", str(tmp_path))
        try:
            async with Session() as db:
                db.add(RetentionPolicy(
                    id="policy", dataset="source_records", retain_days=30,
                    archive_before_purge=True, updated_by="it",
                ))
                for identifier in ("src-1", "src-2", "src-3"):
                    db.add(SourceRecord(
                        id=identifier,
                        source_system="POS",
                        source_version="1",
                        source_record_id=identifier,
                        delivery_id="delivery",
                        payload=b"{}",
                        payload_hash="0" * 64,
                        received_at=old,
                        source_event_time=old,
                        status="Processed",
                        created_at=old,
                    ))
                await db.commit()

                candidate = (
                    await db.execute(
                        select(*SourceRecord.__table__.columns)
                        .where(SourceRecord.id == "src-1")
                    )
                ).first()
                serialized_size = len(
                    (json.dumps(serialise_row(candidate._mapping), sort_keys=True) + "\n").encode()
                )
                monkeypatch.setattr(
                    "apps.operations.routes.MAX_RETENTION_ARCHIVE_BYTES",
                    serialized_size + 1,
                )

                result = await execute_retention(
                    "source_records", RetentionRunBody(confirm_purge=True), db, {"user": "it.user"}
                )

                assert result["eligible_records"] == result["archived_records"] == 1
                assert result["purged_records"] == 1
                assert result["more_eligible"] is True
                remaining = (await db.execute(select(SourceRecord.id))).scalars().all()
                assert remaining == ["src-2", "src-3"]
                with open(result["archive_location"]) as archive_file:
                    rows = [json.loads(line) for line in archive_file]
                assert [row["id"] for row in rows] == ["src-1"]
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_retention_removes_archive_when_purge_fails(tmp_path, monkeypatch):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setenv("NIMBUS_ARCHIVE_DIR", str(tmp_path))

        async def fail_purge(*args):
            raise RuntimeError("simulated purge failure")

        monkeypatch.setattr("apps.operations.routes.purge_retention_candidates", fail_purge)
        try:
            async with Session() as db:
                old = utcnow() - timedelta(days=45)
                db.add(RetentionPolicy(
                    id="policy", dataset="source_records", retain_days=30,
                    archive_before_purge=True, updated_by="it",
                ))
                db.add(SourceRecord(
                    id="src-1", source_system="POS", source_version="1",
                    source_record_id="src-1", delivery_id="delivery", payload=b"{}",
                    payload_hash="0" * 64, received_at=old, source_event_time=old,
                    status="Processed", created_at=old,
                ))
                await db.commit()

                with pytest.raises(RuntimeError, match="simulated purge failure"):
                    await execute_retention(
                        "source_records", RetentionRunBody(confirm_purge=True), db, {"user": "it.user"}
                    )

                await db.rollback()
                assert await db.scalar(select(func.count()).select_from(SourceRecord)) == 1
                assert list(tmp_path.rglob("*.jsonl")) == []
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_empty_retention_run_is_written_to_audit_log():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with Session() as db:
                db.add(RetentionPolicy(
                    id="policy", dataset="source_records", retain_days=30,
                    archive_before_purge=False, updated_by="it",
                ))
                await db.commit()

                result = await execute_retention(
                    "source_records", RetentionRunBody(confirm_purge=False), db, {"user": "it.user"}
                )

                audit_event = (await db.execute(
                    select(AuditEvent).where(AuditEvent.event_type == "RETENTION_RUN")
                )).scalar_one()
                assert result["eligible_records"] == 0
                assert audit_event.object_type == "RetentionRun"
                assert audit_event.object_id == result["run_id"]
                assert audit_event.actor == "it.user"
                assert "archived 0, purged 0" in audit_event.action_description
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_retention_refuses_to_purge_transactions_with_referenced_details(tmp_path, monkeypatch):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setenv("NIMBUS_ARCHIVE_DIR", str(tmp_path))
        try:
            async with Session() as db:
                old = utcnow() - timedelta(days=45)
                db.add(RetentionPolicy(
                    id="policy", dataset="canonical_transactions", retain_days=30,
                    archive_before_purge=True, updated_by="it",
                ))
                db.add(CanonicalTransaction(
                    id="tx1", business_date="2026-08-01", transaction_date=old,
                    event_timestamp=old, processing_timestamp=old, settlement_date="2026-08-01",
                    transaction_type="Sale", source_lineage="pos-map-v1", currency="USD",
                    signed_amount=Decimal("10"), store_id="S1", channel_id="store",
                    reconciliation_status="Open", disposition="Open", created_at=old,
                ))
                db.add(TransactionLine(
                    id="line1", transaction_id="tx1", line_no=1, item_code="SKU-1",
                    quantity=Decimal("1"), unit_price=Decimal("10"), gross_amount=Decimal("10"),
                ))
                await db.commit()
                with pytest.raises(HTTPException) as error:
                    await execute_retention(
                        "canonical_transactions", RetentionRunBody(confirm_purge=True), db, {"user": "it.user"}
                    )
                assert error.value.status_code == 409
                assert await db.scalar(select(func.count()).select_from(CanonicalTransaction)) == 1
                assert await db.scalar(select(func.count()).select_from(TransactionLine)) == 1
                assert not list(tmp_path.rglob("*.jsonl"))
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_retention_refuses_to_purge_referenced_source_records(tmp_path, monkeypatch):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setenv("NIMBUS_ARCHIVE_DIR", str(tmp_path))
        try:
            async with Session() as db:
                old = utcnow() - timedelta(days=45)
                db.add(RetentionPolicy(
                    id="source-policy", dataset="source_records", retain_days=30,
                    archive_before_purge=True, updated_by="it",
                ))
                db.add(SourceRecord(
                    id="source-1", source_system="POS", source_version="1", source_record_id="pos-1",
                    delivery_id="delivery-1", payload=b"{}", payload_hash="0" * 64,
                    received_at=old, source_event_time=old, status="Processed",
                ))
                db.add(CanonicalTransaction(
                    id="tx-source-1", business_date="2026-08-01", transaction_date=old,
                    event_timestamp=old, processing_timestamp=old, settlement_date="2026-08-01",
                    transaction_type="Sale", source_lineage="pos-map-v1", currency="USD",
                    signed_amount=Decimal("10"), store_id="S1", channel_id="store",
                    reconciliation_status="Open", disposition="Open", source_record_id="source-1",
                ))
                await db.commit()
                with pytest.raises(HTTPException) as error:
                    await execute_retention(
                        "source_records", RetentionRunBody(confirm_purge=True), db, {"user": "it.user"}
                    )
                assert error.value.status_code == 409
                assert await db.scalar(select(func.count()).select_from(SourceRecord)) == 1
                assert await db.scalar(select(func.count()).select_from(CanonicalTransaction)) == 1
                assert not list(tmp_path.rglob("*.jsonl"))
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_retention_never_purges_hash_chained_audit_events():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with Session() as db:
                old = utcnow() - timedelta(days=45)
                db.add(RetentionPolicy(
                    id="audit-policy", dataset="audit_events", retain_days=30,
                    archive_before_purge=False, updated_by="it",
                ))
                db.add(AuditEvent(
                    id="audit-1", event_type="TEST", actor="test", object_type="Case",
                    object_id="case-1", action_description="test event", created_at=old,
                ))
                await db.commit()
                preview = await preview_retention("audit_events", db, {"user": "it.user"})
                assert preview["retention_supported"] is False
                with pytest.raises(HTTPException, match="append-only and hash-chained") as error:
                    await set_retention(
                        "audit_events", RetentionBody(retain_days=60), db, {"user": "it.user"}
                    )
                assert error.value.status_code == 409
                for confirm_purge in (False, True):
                    with pytest.raises(HTTPException, match="append-only and hash-chained") as error:
                        await execute_retention(
                            "audit_events", RetentionRunBody(confirm_purge=confirm_purge), db, {"user": "it.user"}
                        )
                    assert error.value.status_code == 409
                assert await db.scalar(select(func.count()).select_from(AuditEvent)) == 1
        finally:
            await engine.dispose()

    asyncio.run(run())
