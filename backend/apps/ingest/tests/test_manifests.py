import asyncio

from apps.ingest import controls, reconciliation, service
from apps.ingest.manifests import iter_manifests, manifest_query
from models import Base, CanonicalTransaction, SourceRecord, TransactionAdjustment
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


def test_reconciliation_loads_only_requested_manifest_date():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session = async_sessionmaker(engine, expire_on_commit=False)
            async with session() as db:
                await service.ingest(
                    db,
                    "POSControl",
                    "d1",
                    [
                        {
                            "source_record_id": "day1",
                            "store_id": "S1",
                            "business_date": "2026-10-01",
                            "declared_count": 1,
                            "declared_total": "10",
                        },
                        {
                            "source_record_id": "day2",
                            "store_id": "S1",
                            "business_date": "2026-10-02",
                            "declared_count": 1,
                            "declared_total": "20",
                        },
                    ],
                )
                query = manifest_query("2026-10-01", ("Archived",))
                assert "source_event_time >=" in str(query) and "source_event_time <" in str(query)
                rows = (await db.execute(query)).scalars().all()
                assert [row.source_record_id for row in rows] == ["day1"]
                findings = []
                await reconciliation.run_controls(
                    db, "2026-10-01", lambda *args: findings.append(args)
                )
                assert findings and all(item[2] == "2026-10-01" for item in findings)
                remaining = (
                    (await db.execute(manifest_query("2026-10-02", ("Archived",)))).scalars().all()
                )
                assert [row.source_record_id for row in remaining] == ["day2"]
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_manifest_iterator_pages_without_skipping_or_repeating(monkeypatch):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session = async_sessionmaker(engine, expire_on_commit=False)
            async with session() as db:
                await service.ingest(
                    db,
                    "POSControl",
                    "many",
                    [
                        {
                            "source_record_id": f"manifest-{index:04d}",
                            "store_id": "S1",
                            "business_date": "2026-10-01",
                            "declared_count": 0,
                            "declared_total": "0",
                        }
                        for index in range(1001)
                    ],
                )
                monkeypatch.setattr("apps.ingest.manifests.MANIFEST_PAGE_SIZE", 137)
                ids = [row.id async for row in iter_manifests(db, "2026-10-01", ("Archived",))]
                assert len(ids) == 1001
                assert len(set(ids)) == 1001
                assert ids == sorted(ids)
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_manifest_page_query_uses_composite_source_status_id_index():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
                plan = await connection.exec_driver_sql(
                    "EXPLAIN QUERY PLAN SELECT id FROM source_records "
                    "WHERE source_system = 'POSControl' AND status = 'Archived' AND id > '' "
                    "ORDER BY id LIMIT 500"
                )
                details = " ".join(row[3] for row in plan.fetchall())
                assert "ix_source_records_system_status_id" in details
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_delivery_lookup_uses_composite_source_delivery_index():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
                plan = await connection.exec_driver_sql(
                    "EXPLAIN QUERY PLAN SELECT id FROM source_records "
                    "WHERE source_system = 'POS' AND delivery_id = 'delivery-1'"
                )
                details = " ".join(row[3] for row in plan.fetchall())
                assert "ix_source_records_system_delivery" in details
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_source_identity_lookup_uses_composite_index():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
                plan = await connection.exec_driver_sql(
                    "EXPLAIN QUERY PLAN SELECT source_record_id, payload_hash FROM source_records "
                    "WHERE source_system = 'POS' AND source_record_id IN ('record-1', 'record-2')"
                )
                details = " ".join(row[3] for row in plan.fetchall())
                assert "ix_source_records_system_record_id" in details
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_pos_control_sums_large_scopes_in_pages_with_approved_corrections():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session = async_sessionmaker(engine, expire_on_commit=False)
            async with session() as db:
                records = [
                    {
                        "source_record_id": f"sale-{index:04d}",
                        "record_type": "Sale",
                        "event_time": f"2026-10-01T10:{index % 60:02d}:00Z",
                        "amount": "1",
                        "store_id": "S1",
                    }
                    for index in range(501)
                ]
                await service.ingest(db, "POS", "sales", records)
                transaction = (
                    await db.execute(
                        select(CanonicalTransaction).order_by(CanonicalTransaction.id).limit(1)
                    )
                ).scalar_one()
                db.add(
                    TransactionAdjustment(
                        id="approved-correction",
                        transaction_id=transaction.id,
                        adjustment_type="Amount",
                        proposed_values={"amount_delta": "1"},
                        status="Approved",
                        rationale="Verified source discrepancy",
                        proposed_by="analyst",
                    )
                )
                await service.ingest(
                    db,
                    "POSControl",
                    "control",
                    [
                        {
                            "source_record_id": "manifest",
                            "store_id": "S1",
                            "business_date": "2026-10-01",
                            "declared_count": 501,
                            "declared_total": "502",
                        }
                    ],
                )

                findings = []
                await reconciliation.run_controls(
                    db, "2026-10-01", lambda *finding: findings.append(finding)
                )

                assert findings == []
                manifest = (
                    await db.execute(
                        select(SourceRecord).where(SourceRecord.source_record_id == "manifest")
                    )
                ).scalar_one()
                assert manifest.status == "Processed"
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_control_reconciliation_batches_store_date_currency_queries(monkeypatch):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                day = "2026-10-01"
                await service.ingest(
                    db,
                    "POS",
                    "sales",
                    [
                        {
                            "source_record_id": f"sale-{index}",
                            "record_type": "Sale",
                            "amount": "10.00",
                            "event_time": f"{day}T10:00:00Z",
                            "store_id": f"S{index}",
                        }
                        for index in range(5)
                    ],
                )
                await service.ingest(
                    db,
                    "POSControl",
                    "controls",
                    [
                        {
                            "source_record_id": f"manifest-{index}",
                            "business_date": day,
                            "store_id": f"S{index}",
                            "declared_count": 1,
                            "declared_total": "10.00",
                        }
                        for index in range(5)
                    ],
                )
                monkeypatch.setattr(controls, "COMPOSITE_SCOPE_BATCH_SIZE", 2)
                queries = []

                def count_transaction_queries(
                    _connection, _cursor, statement, _parameters, _context, _executemany
                ):
                    if "from canonical_transactions" in statement.lower():
                        queries.append(statement)

                event.listen(engine.sync_engine, "before_cursor_execute", count_transaction_queries)
                findings = []
                try:
                    await reconciliation.run_controls(
                        db, day, lambda *finding: findings.append(finding)
                    )
                finally:
                    event.remove(
                        engine.sync_engine, "before_cursor_execute", count_transaction_queries
                    )

                assert findings == []
                assert len(queries) <= 9
                manifests = (
                    (
                        await db.execute(
                            select(SourceRecord).where(SourceRecord.source_system == "POSControl")
                        )
                    )
                    .scalars()
                    .all()
                )
                assert all(manifest.status == "Processed" for manifest in manifests)
        finally:
            await engine.dispose()

    asyncio.run(run())
