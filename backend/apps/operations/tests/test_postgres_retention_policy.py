"""Retention policy locking checks; set ZETSA_TEST_POSTGRES_URL to run."""

import asyncio
import os
import uuid
from datetime import timedelta

import pytest
from apps.operations import routes
from fastapi import HTTPException
from models import AuditEvent, Base, RetentionPolicy, SourceRecord, utcnow
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

POSTGRES_URL = os.getenv("ZETSA_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="ZETSA_TEST_POSTGRES_URL not configured")


def test_retention_runs_and_policy_changes_are_serialized(monkeypatch):
    async def run():
        schema = "zetsa_test_" + uuid.uuid4().hex
        admin = create_async_engine(POSTGRES_URL)
        engine = None
        try:
            async with admin.begin() as connection:
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            engine = create_async_engine(
                POSTGRES_URL, connect_args={"server_settings": {"search_path": schema}}
            )
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with sessions() as db:
                old = utcnow() - timedelta(days=60)
                db.add_all(
                    [
                        RetentionPolicy(
                            id="policy",
                            dataset="source_records",
                            retain_days=30,
                            archive_before_purge=False,
                            updated_by="it",
                        ),
                        SourceRecord(
                            id="src-1",
                            source_system="POS",
                            source_version="1",
                            source_record_id="src-1",
                            delivery_id="delivery",
                            payload=b"{}",
                            payload_hash="0" * 64,
                            received_at=old,
                            source_event_time=old,
                            status="Processed",
                            created_at=old,
                        ),
                    ]
                )
                await db.commit()

            user = {"user": "it"}
            async with sessions() as updater, sessions() as runner:
                update_ready, release_update = asyncio.Event(), asyncio.Event()
                original_commit = updater.commit

                async def paused_commit():
                    update_ready.set()
                    await release_update.wait()
                    await original_commit()

                monkeypatch.setattr(updater, "commit", paused_commit)
                update_task = asyncio.create_task(
                    routes.set_retention(
                        "source_records",
                        routes.RetentionBody(retain_days=90, archive_before_purge=True),
                        updater,
                        user,
                    )
                )
                try:
                    await asyncio.wait_for(update_ready.wait(), 5)
                    run_task = asyncio.create_task(
                        routes.execute_retention(
                            "source_records",
                            routes.RetentionRunBody(confirm_purge=True),
                            runner,
                            user,
                        )
                    )
                    with pytest.raises(TimeoutError):
                        await asyncio.wait_for(asyncio.shield(run_task), 0.2)
                finally:
                    release_update.set()
                await asyncio.wait_for(update_task, 5)
                result = await asyncio.wait_for(run_task, 5)
                assert result["eligible_records"] == 0

            async with sessions() as db:
                assert await db.get(SourceRecord, "src-1") is not None
                await routes.set_retention(
                    "source_records",
                    routes.RetentionBody(retain_days=30, archive_before_purge=False),
                    db,
                    user,
                )

            original_candidates = routes.retention_candidates
            run_ready, release_run = asyncio.Event(), asyncio.Event()

            async def paused_candidates(*args):
                run_ready.set()
                await release_run.wait()
                return await original_candidates(*args)

            monkeypatch.setattr(routes, "retention_candidates", paused_candidates)
            async with sessions() as runner, sessions() as updater:
                run_task = asyncio.create_task(
                    routes.execute_retention(
                        "source_records", routes.RetentionRunBody(confirm_purge=True), runner, user
                    )
                )
                try:
                    await asyncio.wait_for(run_ready.wait(), 5)
                    update_task = asyncio.create_task(
                        routes.set_retention(
                            "source_records",
                            routes.RetentionBody(retain_days=90, archive_before_purge=True),
                            updater,
                            user,
                        )
                    )
                    with pytest.raises(TimeoutError):
                        await asyncio.wait_for(asyncio.shield(update_task), 0.2)
                finally:
                    release_run.set()
                result = await asyncio.wait_for(run_task, 5)
                await asyncio.wait_for(update_task, 5)
                assert result["purged_records"] == 1

            async with sessions() as db:
                assert await db.get(SourceRecord, "src-1") is None
                policy = (await db.execute(select(RetentionPolicy))).scalar_one()
                assert policy.retain_days == 90 and policy.archive_before_purge

            both_ready, release_runs = asyncio.Event(), asyncio.Event()
            arrived = 0

            async def concurrent_candidates(*args):
                nonlocal arrived
                arrived += 1
                if arrived == 2:
                    both_ready.set()
                await release_runs.wait()
                return await original_candidates(*args)

            monkeypatch.setattr(routes, "retention_candidates", concurrent_candidates)
            async with sessions() as first, sessions() as second:
                tasks = [
                    asyncio.create_task(
                        routes.execute_retention(
                            "source_records", routes.RetentionRunBody(), session, user
                        )
                    )
                    for session in (first, second)
                ]
                try:
                    await asyncio.wait_for(both_ready.wait(), 5)
                finally:
                    release_runs.set()
                    results = await asyncio.wait_for(asyncio.gather(*tasks), 5)
                assert all(result["eligible_records"] == 0 for result in results)
        finally:
            if engine:
                await engine.dispose()
            async with admin.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await admin.dispose()

    asyncio.run(run())


def test_concurrent_first_policy_creation_returns_a_retryable_conflict(monkeypatch):
    async def run():
        schema = "zetsa_test_" + uuid.uuid4().hex
        admin = create_async_engine(POSTGRES_URL)
        engine = None
        try:
            async with admin.begin() as connection:
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            engine = create_async_engine(
                POSTGRES_URL, connect_args={"server_settings": {"search_path": schema}}
            )
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            original_lock = routes.lock_retention_policy
            barrier = asyncio.Barrier(2)

            async def synchronize_creators(db, dataset, *, shared=False):
                policy = await original_lock(db, dataset, shared=shared)
                assert policy is None
                await barrier.wait()
                return policy

            monkeypatch.setattr(routes, "lock_retention_policy", synchronize_creators)
            async with sessions() as first, sessions() as second:
                session_list = (first, second)
                bodies = (
                    routes.RetentionBody(retain_days=30),
                    routes.RetentionBody(retain_days=90),
                )
                outcomes = await asyncio.wait_for(
                    asyncio.gather(
                        *(
                            routes.set_retention("source_records", body, session, {"user": "it"})
                            for session, body in zip(session_list, bodies, strict=True)
                        ),
                        return_exceptions=True,
                    ),
                    10,
                )
                conflicts = [
                    index
                    for index, result in enumerate(outcomes)
                    if isinstance(result, HTTPException)
                ]
                assert len(conflicts) == 1
                conflict = outcomes[conflicts[0]]
                assert conflict.status_code == 409 and "retry" in conflict.detail
                created = outcomes[1 - conflicts[0]]
                assert isinstance(created, dict)

                monkeypatch.setattr(routes, "lock_retention_policy", original_lock)
                retry = await routes.set_retention(
                    "source_records",
                    bodies[conflicts[0]],
                    session_list[conflicts[0]],
                    {"user": "it"},
                )
                assert retry["retain_days"] == bodies[conflicts[0]].retain_days

            async with sessions() as db:
                assert await db.scalar(select(func.count()).select_from(RetentionPolicy)) == 1
                events = (
                    (
                        await db.execute(
                            select(AuditEvent)
                            .where(AuditEvent.object_type == "RetentionPolicy")
                            .order_by(AuditEvent.seq)
                        )
                    )
                    .scalars()
                    .all()
                )
                assert [event.event_type for event in events] == [
                    "RETENTION_POLICY_CREATED",
                    "RETENTION_POLICY_UPDATED",
                ]
                assert (
                    events[1].after_state["previous_settings"]["retain_days"]
                    == created["retain_days"]
                )
        finally:
            if engine:
                await engine.dispose()
            async with admin.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await admin.dispose()

    asyncio.run(run())
