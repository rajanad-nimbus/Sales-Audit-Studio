"""Daily feed reconciliation keeps composite-scope queries within bind limits."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

from apps.ingest import controls, reconciliation
from models import Base, CanonicalTransaction
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


def test_daily_feed_actual_and_expected_queries_batch_composite_scopes(monkeypatch):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                monkeypatch.setattr(controls, "COMPOSITE_SCOPE_BATCH_SIZE", 2)
                timestamp = datetime(2026, 10, 1, tzinfo=UTC)
                feed = []
                transactions = []
                for index in range(5):
                    store_id = f"STORE-{index}"
                    feed_row = CanonicalTransaction(
                        id=f"bank-{index}",
                        business_date="2026-10-01",
                        transaction_date=timestamp,
                        event_timestamp=timestamp,
                        processing_timestamp=timestamp,
                        settlement_date="2026-10-01",
                        transaction_type="Deposit",
                        source_lineage="bank-map-v1",
                        currency="USD",
                        signed_amount=Decimal("10.00"),
                        store_id=store_id,
                        channel_id="store",
                        reconciliation_status="Unmatched",
                        disposition="Open",
                    )
                    expected_row = CanonicalTransaction(
                        id=f"processor-{index}",
                        business_date="2026-10-01",
                        transaction_date=timestamp,
                        event_timestamp=timestamp,
                        processing_timestamp=timestamp,
                        settlement_date="2026-10-01",
                        transaction_type="Capture",
                        source_lineage="processor-map-v1",
                        currency="USD",
                        signed_amount=Decimal("10.00"),
                        store_id=store_id,
                        channel_id="store",
                        reconciliation_status="Matched",
                        disposition="Closed",
                    )
                    feed.append(feed_row)
                    transactions.extend((feed_row, expected_row))
                db.add_all(transactions)
                await db.flush()

                scoped_queries = []

                def capture_scoped_query(
                    _connection, _cursor, statement, parameters, _context, _executemany
                ):
                    normalized = statement.lower()
                    if (
                        " in (values " in normalized
                        and "canonical_transactions.store_id" in normalized
                    ):
                        scoped_queries.append((statement, parameters))

                event.listen(engine.sync_engine, "before_cursor_execute", capture_scoped_query)
                try:
                    actual, expected = await reconciliation.collect_daily_feed_amounts(
                        db, feed, "Processor"
                    )
                finally:
                    event.remove(engine.sync_engine, "before_cursor_execute", capture_scoped_query)

                assert actual == {
                    (f"STORE-{index}", "USD", "2026-10-01"): Decimal("10.00") for index in range(5)
                }
                assert expected == actual
                assert len(scoped_queries) == 12  # 3 scope batches, with a terminal page per query.
                assert max(len(parameters) for _, parameters in scoped_queries) <= 12
        finally:
            await engine.dispose()

    asyncio.run(run())
