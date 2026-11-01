import asyncio

import pytest
from fastapi import HTTPException
from main import connector_action
from models import Base, Connector
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.assistant.chat import rule_answer
from apps.system.connectors import DEFAULT_CONNECTORS, ensure_default_connectors


def test_default_connectors_do_not_claim_unverified_health():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                await ensure_default_connectors(db)
                rows = (
                    (await db.execute(select(Connector).order_by(Connector.name))).scalars().all()
                )
                assert {row.name for row in rows} == set(DEFAULT_CONNECTORS)
                assert all(row.state == "Unknown" for row in rows)
                assert all(row.mode == "Not Configured" for row in rows)
                assert all(row.last_checked_at is None for row in rows)
                await ensure_default_connectors(db)
                assert len((await db.execute(select(Connector))).scalars().all()) == len(
                    DEFAULT_CONNECTORS
                )
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_connector_action_does_not_claim_a_retry_or_pause_succeeded():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add(
                    Connector(
                        id="connector", name="POS Feed", state="Unknown", mode="Not Configured"
                    )
                )
                await db.commit()
                for action in ("retry", "pause", "resume"):
                    with pytest.raises(HTTPException) as error:
                        await connector_action("connector", action, db, {"user": "operator"})
                    assert error.value.status_code == 501
                row = await db.get(Connector, "connector")
                assert (row.state, row.mode, row.last_checked_at) == (
                    "Unknown",
                    "Not Configured",
                    None,
                )
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_assistant_does_not_report_unconfigured_connectors_as_healthy_or_retryable():
    answer = rule_answer(
        "How are the connectors?",
        {
            "connectors": [{"name": "POS Feed", "state": "Unknown", "mode": "Not Configured"}],
        },
    )

    assert "0 of 1 connectors are healthy" in answer["answer"]
    assert "Configure the source adapters" in answer["answer"]
    assert "Retry or pause" not in answer["answer"]
