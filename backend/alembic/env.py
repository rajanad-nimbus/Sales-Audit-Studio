import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import logging

from alembic import context
from database import DATABASE_URL
from models import Base
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

target_metadata = Base.metadata
logger = logging.getLogger("alembic.runtime.migration")
MIGRATION_LOCK_KEYS = (1313164626, 1296387405)


def run_migrations_offline() -> None:
    context.configure(url=DATABASE_URL, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def do_run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(DATABASE_URL)
    try:
        async with engine.connect() as connection:
            locked = connection.dialect.name == "postgresql"
            try:
                if locked:
                    await connection.execute(
                        text("SELECT pg_advisory_lock(:key1, :key2)"),
                        {"key1": MIGRATION_LOCK_KEYS[0], "key2": MIGRATION_LOCK_KEYS[1]},
                    )
                    await connection.commit()
                await connection.run_sync(do_run)
            finally:
                if locked:
                    try:
                        await connection.execute(
                            text("SELECT pg_advisory_unlock(:key1, :key2)"),
                            {"key1": MIGRATION_LOCK_KEYS[0], "key2": MIGRATION_LOCK_KEYS[1]},
                        )
                        await connection.commit()
                    except Exception:
                        logger.exception("Could not release the PostgreSQL migration advisory lock")
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
