"""Bulk investigations load their exception evidence with one grouped query."""
import asyncio
from decimal import Decimal

from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.agents import action_center
from models import Base, Case, Exception as DBException


def test_bulk_investigation_fetches_exceptions_once_and_groups_them_by_case(monkeypatch):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                for case_id in ("case-1", "case-2", "case-3"):
                    db.add(Case(
                        id=case_id,
                        case_number=case_id.upper(),
                        case_type="test",
                        business_date="2026-10-01",
                        store_id="S1",
                    ))
                    db.add(DBException(
                        id=f"exception-{case_id}",
                        case_id=case_id,
                        exception_type="UNMATCHED_SALE",
                        exception_family="Reconciliation",
                        source_system="POS",
                        detection_origin="Test",
                        detection_rule_id="TEST-UNMATCHED-SALE",
                        exception_amount=Decimal("10.00"),
                        estimated_exposure=Decimal("10.00"),
                        severity="Medium",
                        confidence="High",
                        close_impact="Yes",
                    ))
                await db.commit()

                received = []

                async def fake_investigation(_db, case, exceptions, **_kwargs):
                    received.append((case.id, [exception.id for exception in exceptions]))

                monkeypatch.setattr(action_center.investigation_service, "run_investigation", fake_investigation)
                exception_queries = 0

                def count_exception_queries(_connection, _cursor, statement, _parameters, _context, _executemany):
                    nonlocal exception_queries
                    if "FROM EXCEPTIONS" in statement.upper():
                        exception_queries += 1

                event.listen(engine.sync_engine, "before_cursor_execute", count_exception_queries)
                try:
                    result = await action_center.bulk(
                        action_center.BulkRequest(action="investigate", ids=["case-1", "case-2", "case-3"]),
                        db,
                        {"user": "finance", "role": "finance"},
                    )
                finally:
                    event.remove(engine.sync_engine, "before_cursor_execute", count_exception_queries)

                assert result["ok"] == 3
                assert exception_queries == 1
                assert received == [
                    ("case-1", ["exception-case-1"]),
                    ("case-2", ["exception-case-2"]),
                    ("case-3", ["exception-case-3"]),
                ]
        finally:
            await engine.dispose()

    asyncio.run(run())
