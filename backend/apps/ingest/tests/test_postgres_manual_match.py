"""Manual-match review concurrency; set ZETSA_TEST_POSTGRES_URL to run."""

import asyncio
import os
import uuid

import pytest
from apps.ingest import reconciliation, routes, service
from fastapi import HTTPException
from models import Base, CanonicalTransaction, ReconciliationMatch
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

POSTGRES_URL = os.getenv("ZETSA_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="ZETSA_TEST_POSTGRES_URL not configured")


def test_only_one_concurrent_manual_match_review_wins():
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
                await service.ingest(
                    db,
                    "POS",
                    "delivery",
                    [
                        {
                            "source_record_id": str(i),
                            "record_type": "Sale",
                            "event_time": "2026-10-01T10:00:00Z",
                            "amount": "10",
                            "store_id": "S1",
                        }
                        for i in range(2)
                    ],
                )
                ids = (await db.execute(select(CanonicalTransaction.id))).scalars().all()
                db.add(
                    ReconciliationMatch(
                        id="match",
                        transaction_ids=list(ids),
                        match_type="Manual",
                        rationale="test",
                        proposed_by="user",
                    )
                )
                await db.commit()
            async with sessions() as first, sessions() as second:
                results = await asyncio.gather(
                    routes.approve_manual_match("match", first, {"user": "a"}),
                    routes.reject_manual_match("match", second, {"user": "b"}),
                    return_exceptions=True,
                )
                assert sum(isinstance(result, dict) for result in results) == 1
                assert (
                    sum(
                        isinstance(result, HTTPException) and result.status_code == 409
                        for result in results
                    )
                    == 1
                )
            async with sessions() as db:
                match = await db.get(ReconciliationMatch, "match")
                transactions = (await db.execute(select(CanonicalTransaction))).scalars().all()
                assert all(
                    (row.reconciliation_status == "Manually Matched")
                    == (match.status == "Approved")
                    for row in transactions
                )
                await service.ingest(
                    db,
                    "POS",
                    "second",
                    [
                        {
                            "source_record_id": f"overlap-{i}",
                            "record_type": "Sale",
                            "event_time": "2026-10-01T10:00:00Z",
                            "amount": "10",
                            "store_id": "S2",
                        }
                        for i in range(3)
                    ],
                )
                ids = (
                    (
                        await db.execute(
                            select(CanonicalTransaction.id)
                            .where(CanonicalTransaction.store_id == "S2")
                            .order_by(CanonicalTransaction.id)
                        )
                    )
                    .scalars()
                    .all()
                )
                db.add_all(
                    [
                        ReconciliationMatch(
                            id="left",
                            transaction_ids=list(ids[:2]),
                            match_type="Manual",
                            rationale="test",
                            proposed_by="user",
                        ),
                        ReconciliationMatch(
                            id="right",
                            transaction_ids=list(ids[1:]),
                            match_type="Manual",
                            rationale="test",
                            proposed_by="user",
                        ),
                    ]
                )
                await db.commit()
            async with sessions() as first, sessions() as second:
                results = await asyncio.gather(
                    routes.approve_manual_match("left", first, {"user": "a"}),
                    routes.approve_manual_match("right", second, {"user": "b"}),
                    return_exceptions=True,
                )
                assert sum(isinstance(result, dict) for result in results) == 1
                assert (
                    sum(
                        isinstance(result, HTTPException) and result.status_code == 409
                        for result in results
                    )
                    == 1
                )
            async with sessions() as db:
                statuses = (
                    (
                        await db.execute(
                            select(ReconciliationMatch.status).where(
                                ReconciliationMatch.id.in_(["left", "right"])
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                assert sorted(statuses) == ["Approved", "Proposed"]
        finally:
            if engine:
                await engine.dispose()
            async with admin.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await admin.dispose()

    asyncio.run(run())


def test_reconciliation_runs_serialize_across_database_sessions():
    async def run():
        engine = create_async_engine(POSTGRES_URL)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions() as first, sessions() as second:
                await reconciliation.lock_reconciliation(first)
                waiting = asyncio.create_task(reconciliation.lock_reconciliation(second))
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(asyncio.shield(waiting), 0.2)
                await first.commit()
                await waiting
                await second.commit()
        finally:
            await engine.dispose()

    asyncio.run(run())
