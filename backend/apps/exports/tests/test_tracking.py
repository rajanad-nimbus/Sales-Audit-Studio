import asyncio
from datetime import UTC, datetime

from models import Base, Case, ExportBatch, ExportDestination, ExportItem
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.exports.routes import tracking_page


def test_tracking_projection_counts_states_and_returns_a_stable_page():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add(
                    ExportDestination(
                        id="destination", name="ERP", kind="file", format="json", config={}
                    )
                )
                db.add(
                    ExportBatch(
                        id="batch",
                        destination_id="destination",
                        dataset="resolutions",
                        status="Sent",
                        idempotency_key="key",
                        created_by="it",
                    )
                )
                db.add_all(
                    [
                        Case(
                            id="waiting",
                            case_number="C-001",
                            case_type="test",
                            status="Closed",
                            store_id="S2",
                            business_date="2026-10-01",
                            updated_at=datetime(2026, 10, 1, tzinfo=UTC),
                        ),
                        Case(
                            id="backpost",
                            case_number="C-002",
                            case_type="test",
                            status="Closed",
                            store_id="S1",
                            business_date="2026-09-30",
                            updated_at=datetime(2026, 10, 1, tzinfo=UTC),
                        ),
                        Case(
                            id="delivered",
                            case_number="C-003",
                            case_type="test",
                            status="Closed",
                            store_id="S1",
                            business_date="2026-10-01",
                            updated_at=datetime(2026, 10, 1, tzinfo=UTC),
                        ),
                        Case(
                            id="open",
                            case_number="C-004",
                            case_type="test",
                            status="Open",
                            store_id="S1",
                            business_date="2026-10-01",
                            updated_at=datetime(2026, 10, 1, tzinfo=UTC),
                        ),
                    ]
                )
                db.add(
                    ExportItem(
                        id="item",
                        batch_id="batch",
                        destination_id="destination",
                        dataset="resolutions",
                        object_id="delivered",
                        object_version="v1",
                        state="Delivered",
                        record_ref="C-003",
                    )
                )
                await db.commit()

                summary, page, total = await tracking_page(
                    db, "destination", "resolutions", "", "", 1, 2
                )
                assert summary == {
                    "delivered": 1,
                    "in_batch": 0,
                    "waiting": 2,
                    "not_eligible": 1,
                    "waiting_backposts": 1,
                }
                assert total == 4
                assert [item["object_id"] for item in page] == ["backpost", "waiting"]
                assert page[0]["state"] == "waiting" and page[0]["backpost"]
                assert page[1]["state"] == "waiting" and not page[1]["backpost"]
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_tracking_pages_tie_break_duplicate_record_references_by_object_id():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add(
                    ExportDestination(
                        id="destination", name="ERP", kind="file", format="json", config={}
                    )
                )
                db.add(
                    ExportBatch(
                        id="batch",
                        destination_id="destination",
                        dataset="resolutions",
                        status="Pending",
                        idempotency_key="key",
                        created_by="it",
                    )
                )
                db.add_all(
                    [
                        Case(
                            id="b-case",
                            case_number="C-002",
                            case_type="test",
                            status="Closed",
                            store_id="S1",
                            business_date="2026-10-01",
                        ),
                        Case(
                            id="a-case",
                            case_number="C-001",
                            case_type="test",
                            status="Closed",
                            store_id="S1",
                            business_date="2026-10-01",
                        ),
                    ]
                )
                db.add_all(
                    [
                        ExportItem(
                            id=f"item-{case_id}",
                            batch_id="batch",
                            destination_id="destination",
                            dataset="resolutions",
                            object_id=case_id,
                            object_version="v1",
                            state="Pending",
                            record_ref="SHARED-REFERENCE",
                        )
                        for case_id in ("b-case", "a-case")
                    ]
                )
                await db.commit()

                statements = []

                def capture(_connection, _cursor, statement, _parameters, _context, _executemany):
                    if "FROM tracking_rows" in statement and "OFFSET" in statement:
                        statements.append(statement)

                event.listen(engine.sync_engine, "before_cursor_execute", capture)
                try:
                    _, first, total = await tracking_page(
                        db, "destination", "resolutions", "", "", 1, 1
                    )
                    _, second, _ = await tracking_page(
                        db, "destination", "resolutions", "", "", 2, 1
                    )
                finally:
                    event.remove(engine.sync_engine, "before_cursor_execute", capture)

                assert total == 2
                assert [item["object_id"] for item in first + second] == ["a-case", "b-case"]
                assert len(statements) == 2
                assert all("tracking_rows.object_id" in statement for statement in statements)
        finally:
            await engine.dispose()

    asyncio.run(run())
