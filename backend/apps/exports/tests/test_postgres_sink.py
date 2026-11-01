"""Test the receiver's concurrent retry handling with PostgreSQL."""

import asyncio
import os
import uuid

import pytest
from models import Base
from models import TestSinkReceipt as SinkReceipt
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from apps.exports import routes

POSTGRES_URL = os.getenv("ZETSA_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="ZETSA_TEST_POSTGRES_URL not configured")


def test_concurrent_identical_deliveries_share_one_receipt():
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
            body, key = b"payload", "a" * 64

            async def receive():
                return {"type": "http.request", "body": body, "more_body": False}

            async def send():
                async with sessions() as db:
                    request = Request(
                        {
                            "type": "http",
                            "method": "POST",
                            "path": "/api/exports/_sink",
                            "headers": [],
                        },
                        receive,
                    )
                    return await routes.sink(
                        request,
                        routes.sign(routes.SINK_SECRET, body),
                        key,
                        "resolutions",
                        db,
                        routes.sign_envelope(routes.SINK_SECRET, "resolutions", key, body),
                    )

            first, second = await asyncio.wait_for(asyncio.gather(send(), send()), timeout=5)
            assert first == second
            async with sessions() as db:
                assert (
                    await db.execute(select(func.count()).select_from(SinkReceipt))
                ).scalar_one() == 1
        finally:
            if engine:
                await engine.dispose()
            async with admin.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await admin.dispose()

    asyncio.run(run())
