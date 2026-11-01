"""PostgreSQL audit-chain concurrency coverage; set ZETSA_TEST_POSTGRES_URL to enable."""

import asyncio
import importlib.util
import os
import uuid
from pathlib import Path

import pytest
from models import AuditEvent, Base
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

POSTGRES_URL = os.getenv("ZETSA_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="ZETSA_TEST_POSTGRES_URL not configured")


def _audit_insert_trigger_sql() -> str:
    migration_path = Path(__file__).resolve().parents[3] / "alembic" / "versions" / "0049_audit_chain_sequence_order.py"
    spec = importlib.util.spec_from_file_location("audit_chain_sequence_migration", migration_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load audit chain migration")
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration.AUDIT_INSERT_TRIGGER


def test_concurrent_inserts_keep_sequence_and_hash_chain_ordered():
    async def run():
        schema = "zetsa_audit_test_" + uuid.uuid4().hex
        admin = create_async_engine(POSTGRES_URL)
        engine = None
        try:
            async with admin.begin() as connection:
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            engine = create_async_engine(
                POSTGRES_URL,
                connect_args={"server_settings": {"search_path": schema}},
                pool_size=20,
            )
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
                await connection.execute(text("CREATE SEQUENCE audit_events_seq"))
                await connection.execute(text("""
                    CREATE FUNCTION audit_event_hash(prev text, e audit_events) RETURNS text AS $$
                    SELECT encode(sha256(convert_to(concat_ws('|', coalesce(prev, 'GENESIS'), e.seq::text,
                        e.id, e.event_type, e.actor, e.object_type, e.object_id, coalesce(e.case_id, ''),
                        e.action_description, coalesce(e.before_state::text, ''), coalesce(e.after_state::text, ''),
                        to_char(e.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US')), 'UTF8')), 'hex')
                    $$ LANGUAGE sql IMMUTABLE
                """))
                await connection.execute(text(_audit_insert_trigger_sql()))
                await connection.execute(text(
                    "CREATE TRIGGER audit_chain BEFORE INSERT ON audit_events "
                    "FOR EACH ROW EXECUTE FUNCTION audit_events_before_insert()"
                ))

            sessions = async_sessionmaker(engine, expire_on_commit=False)

            async def insert_event(index: int) -> None:
                async with sessions() as db:
                    db.add(AuditEvent(
                        id=str(uuid.uuid4()), event_type="CONCURRENCY_TEST", actor=f"worker-{index}",
                        object_type="Test", object_id=str(index), action_description=f"event {index}",
                    ))
                    await db.commit()

            await asyncio.gather(*(insert_event(index) for index in range(50)))

            async with sessions() as db:
                events = (await db.execute(
                    select(AuditEvent.seq, AuditEvent.prev_hash, AuditEvent.hash).order_by(AuditEvent.seq)
                )).all()
                assert len(events) == 50
                assert [event.seq for event in events] == list(range(1, 51))
                assert events[0].prev_hash is None
                assert all(current.prev_hash == previous.hash for previous, current in zip(events, events[1:]))
                hashes_match = await db.scalar(text(
                    "SELECT bool_and(a.hash = audit_event_hash(a.prev_hash, a)) FROM audit_events AS a"
                ))
                assert hashes_match is True
        finally:
            if engine is not None:
                await engine.dispose()
            async with admin.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await admin.dispose()

    asyncio.run(run())
