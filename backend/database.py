import os

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://user:password@localhost:5432/dbname",
)


def _integer_setting(name: str, default: int, *, minimum: int = 0) -> int:
    raw_value = os.getenv(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value < minimum:
        raise RuntimeError(f"{name} must be at least {minimum}")
    return value


engine_options = {
    "echo": os.getenv("SQL_ECHO", "False").lower() == "true",
    "pool_pre_ping": True,
    "pool_recycle": _integer_setting("NIMBUS_DB_POOL_RECYCLE_SECONDS", 1800, minimum=1),
}
if DATABASE_URL.startswith("postgresql+"):
    engine_options.update(
        pool_size=_integer_setting("NIMBUS_DB_POOL_SIZE", 10, minimum=1),
        max_overflow=_integer_setting("NIMBUS_DB_MAX_OVERFLOW", 10),
        pool_timeout=_integer_setting("NIMBUS_DB_POOL_TIMEOUT_SECONDS", 30, minimum=1),
    )

engine = create_async_engine(DATABASE_URL, **engine_options)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db():
    async with async_session() as session:
        yield session
