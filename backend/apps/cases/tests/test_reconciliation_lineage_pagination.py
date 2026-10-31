"""Large case lineage remains queryable through bounded cursor pages."""
import asyncio
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.cases.evidence import case_reconciliation
from models import AuditEvent, Base, CanonicalTransaction, Case


def test_case_reconciliation_paginates_large_linked_id_sets_in_database():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                case_id = "case-large-lineage"
                timestamp = datetime(2026, 10, 1, tzinfo=timezone.utc)
                transaction_ids = [f"transaction-{index:04}" for index in range(1200)]
                db.add(Case(
                    id=case_id,
                    case_number="CASE-LARGE-LINEAGE",
                    case_type="Amount mismatch",
                    business_date="2026-10-01",
                    store_id="S1",
                ))
                db.add_all([
                    CanonicalTransaction(
                        id=transaction_id,
                        business_date="2026-10-01",
                        transaction_date=timestamp,
                        event_timestamp=timestamp,
                        processing_timestamp=timestamp,
                        settlement_date="2026-10-01",
                        transaction_type="Capture",
                        source_lineage="processor-map-v1",
                        currency="USD",
                        signed_amount=Decimal("1.00"),
                        store_id="S1",
                        channel_id="store",
                        reconciliation_status="Exception",
                        disposition="Awaiting",
                    )
                    for transaction_id in transaction_ids
                ])
                db.add(AuditEvent(
                    id="event-large-lineage",
                    event_type="CASE_CREATED",
                    actor="reconciliation-engine",
                    object_type="Case",
                    object_id=case_id,
                    case_id=case_id,
                    action_description="Large linked reconciliation case",
                    after_state={"transaction_ids": transaction_ids},
                ))
                await db.flush()

                statements = []

                def capture_query(_connection, _cursor, statement, _parameters, _context, _executemany):
                    if "json_each" in statement.lower():
                        statements.append(statement)

                event.listen(engine.sync_engine, "before_cursor_execute", capture_query)
                try:
                    first = await case_reconciliation(
                        case_id, db, {"user": "finance", "role": "finance"}, limit=200
                    )
                    second = await case_reconciliation(
                        case_id,
                        db,
                        {"user": "finance", "role": "finance"},
                        limit=200,
                        cursor_timestamp=first["next_cursor"]["event_timestamp"],
                        cursor_id=first["next_cursor"]["transaction_id"],
                    )
                finally:
                    event.remove(engine.sync_engine, "before_cursor_execute", capture_query)

                assert len(first["records"]) == len(second["records"]) == 200
                assert first["records"][0]["transaction_id"] == transaction_ids[0]
                assert first["records"][-1]["transaction_id"] == transaction_ids[199]
                assert second["records"][0]["transaction_id"] == transaction_ids[200]
                assert second["records"][-1]["transaction_id"] == transaction_ids[399]
                assert first["has_more"] and second["has_more"]
                assert len(statements) == 2
                assert all(" IN (" not in statement.upper() for statement in statements)
        finally:
            await engine.dispose()

    asyncio.run(run())
