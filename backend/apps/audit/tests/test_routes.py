import asyncio
import csv
import io
import json
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.security import authentication as auth
from apps.audit import routes
from apps.csv_export import csv_row
from database import get_db
from models import AuditEvent, Base


def test_csv_export_neutralizes_spreadsheet_formulas():
    cells = ["=1+1", " +SUM(A1:A2)", "-2+3", "@SUM(A1)", "ordinary text", 42]

    exported = next(csv.reader(io.StringIO(csv_row(cells))))

    assert exported == ["'=1+1", "' +SUM(A1:A2)", "'-2+3", "'@SUM(A1)", "ordinary text", "42"]


def test_date_filters_include_last_second_and_exports_never_silently_truncate(monkeypatch):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session = async_sessionmaker(engine, expire_on_commit=False)
            async with session() as db:
                for seq, timestamp in enumerate([
                    datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc),
                    datetime(2026, 10, 1, 23, 59, 59, 999999, tzinfo=timezone.utc),
                    datetime(2026, 10, 2, 0, 0, tzinfo=timezone.utc),
                ], 1):
                    db.add(AuditEvent(id=str(seq), seq=seq, created_at=timestamp, event_type="TEST",
                                      actor="test", object_type="Case", object_id="c", case_id="c",
                                      action_description="test"))
                await db.commit()

            app = FastAPI()
            app.include_router(routes.router)

            async def db_override():
                async with session() as db:
                    yield db

            async def identity():
                return {"user": "finance@example.test", "role": "finance"}

            app.dependency_overrides[get_db] = db_override
            app.dependency_overrides[auth.current_user] = identity
            monkeypatch.setattr(routes, "MAX_EXPORT_EVENTS", 2)
            monkeypatch.setattr(routes, "EXPORT_PAGE_SIZE", 1)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                page = await client.get("/api/audit/page", params={"date_from": "2026-10-01", "date_to": "2026-10-01"})
                assert page.status_code == 200
                assert {item["seq"] for item in page.json()["items"]} == {1, 2}
                recent = await client.get("/api/audit", params={"limit": 1, "case_id": "c"})
                assert recent.status_code == 200
                assert len(recent.json()) == 1
                assert recent.json()[0]["seq"] == 3
                assert recent.json()[0]["action_description"] == "test"
                literal_wildcard = await client.get("/api/audit/page", params={"q": "%"})
                assert literal_wildcard.status_code == 200 and literal_wildcard.json()["total"] == 0
                for path in ("/api/audit/page", "/api/audit/export"):
                    assert (await client.get(path, params={"date_to": "not-a-date"})).status_code == 422
                    assert (await client.get(path, params={"date_from": "2026-10-02", "date_to": "2026-10-01"})).status_code == 422
                    assert (await client.get(path, params={"event_type": ",".join(f"TYPE-{i}" for i in range(51))})).status_code == 422
                    assert (await client.get(path, params={"actor": "a" * 256})).status_code == 422
                    assert (await client.get(path, params={"q": "q" * 501})).status_code == 422
                assert (await client.get("/api/audit/page", params={"page": 10001})).status_code == 422
                oversized = await client.get("/api/audit/export", params={"format": "json"})
                assert oversized.status_code == 413
                assert "narrow the filters" in oversized.json()["detail"]
                exported = await client.get("/api/audit/export", params={"format": "json", "date_to": "2026-10-01"})
                assert exported.status_code == 200
                assert exported.json()["count"] == 2
                assert [event["seq"] for event in exported.json()["events"]] == [1, 2]
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_audit_export_pins_its_head_and_fetches_each_page_independently(monkeypatch):
    monkeypatch.setattr(routes, "EXPORT_PAGE_SIZE", 1)

    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                for seq in (1, 2):
                    db.add(AuditEvent(
                        id=f"event-{seq}", seq=seq, created_at=datetime.now(timezone.utc),
                        event_type="TEST", actor="test", object_type="Case", object_id="case",
                        case_id="case", action_description=f"event {seq}",
                    ))
                await db.commit()
                response = await routes.export(format="json", db=db, user={"user": "finance"})
                chunks = [await anext(response.body_iterator) for _ in range(3)]
                db.add(AuditEvent(
                    id="event-3", seq=3, created_at=datetime.now(timezone.utc), event_type="TEST",
                    actor="test", object_type="Case", object_id="case", case_id="case",
                    action_description="new event after export began",
                ))
                await db.commit()
                async for chunk in response.body_iterator:
                    chunks.append(chunk)
                exported = json.loads("".join(chunks))
                assert exported["count"] == 2
                assert [event["seq"] for event in exported["events"]] == [1, 2]
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_recent_audit_order_uses_chain_sequence_not_wall_clock():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add_all([
                    AuditEvent(id="older-seq", seq=1, created_at=datetime(2026, 10, 2, tzinfo=timezone.utc),
                               event_type="TEST", actor="test", object_type="Test", object_id="one",
                               action_description="older in chain"),
                    AuditEvent(id="newer-seq", seq=2, created_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
                               event_type="TEST", actor="test", object_type="Test", object_id="two",
                               action_description="newer in chain"),
                ])
                await db.commit()
                rows = await routes.list_recent_events(None, 1, db, {})
                assert [row.seq for row in rows] == [2]
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_recent_audit_keyset_pages_are_bounded_and_stable():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add_all([
                    AuditEvent(id=f"event-{seq}", seq=seq, created_at=datetime.now(timezone.utc),
                               event_type="TEST", actor="test", object_type="Test", object_id=str(seq),
                               action_description=f"event {seq}")
                    for seq in range(1, 6)
                ])
                await db.commit()

                newest = await routes.recent_events(2, None, db, {})
                assert [row["seq"] for row in newest["items"]] == [5, 4]
                assert newest["has_more"] is True
                assert newest["next_before_seq"] == 4

                db.add(AuditEvent(
                    id="event-6", seq=6, created_at=datetime.now(timezone.utc), event_type="TEST",
                    actor="test", object_type="Test", object_id="6", action_description="new event",
                ))
                await db.commit()
                older = await routes.recent_events(2, newest["next_before_seq"], db, {})
                assert [row["seq"] for row in older["items"]] == [3, 2]
                assert older["has_more"] is True
                oldest = await routes.recent_events(2, older["next_before_seq"], db, {})
                assert [row["seq"] for row in oldest["items"]] == [1]
                assert oldest["has_more"] is False
                assert oldest["next_before_seq"] is None
        finally:
            await engine.dispose()

    asyncio.run(run())
