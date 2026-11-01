"""PostgreSQL constraint classification; set ZETSA_TEST_POSTGRES_URL to run."""

import asyncio
import os

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from apps.database_errors import violates_unique_constraint

POSTGRES_URL = os.getenv("ZETSA_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="ZETSA_TEST_POSTGRES_URL not configured")


def test_asyncpg_constraint_name_survives_sqlalchemy_translation():
    async def run():
        engine = create_async_engine(POSTGRES_URL)
        try:
            async with engine.connect() as connection:
                await connection.execute(
                    text(
                        "CREATE TEMP TABLE zetsa_error_probe (id integer PRIMARY KEY, code text UNIQUE)"
                    )
                )
                await connection.execute(text("INSERT INTO zetsa_error_probe VALUES (1, 'x')"))
                await connection.commit()
                with pytest.raises(IntegrityError) as caught:
                    await connection.execute(text("INSERT INTO zetsa_error_probe VALUES (2, 'x')"))
                assert violates_unique_constraint(
                    caught.value,
                    constraint_names={"zetsa_error_probe_code_key"},
                    sqlite_messages=set(),
                )
                assert not violates_unique_constraint(
                    caught.value,
                    constraint_names={"zetsa_error_probe_pkey"},
                    sqlite_messages=set(),
                )
        finally:
            await engine.dispose()

    asyncio.run(run())
