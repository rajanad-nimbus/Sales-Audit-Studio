"""Request size limits for correction search inputs."""

import asyncio

import httpx
from database import get_db
from fastapi import FastAPI
from models import Base
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.corrections import routes
from apps.security import authentication


def test_transaction_search_bounds_query_length():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            app = FastAPI()
            app.include_router(routes.router)

            async def database_override():
                async with sessions() as db:
                    yield db

            async def user_override():
                return {"user": "finance@example.test", "role": "finance"}

            app.dependency_overrides[get_db] = database_override
            app.dependency_overrides[authentication.current_user] = user_override
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                accepted = await client.get(
                    "/api/transaction-adjustments/transactions/search",
                    params={"q": "x" * 255},
                )
                rejected = await client.get(
                    "/api/transaction-adjustments/transactions/search",
                    params={"q": "x" * 256},
                )

            assert accepted.status_code == 200
            assert accepted.json() == []
            assert rejected.status_code == 422
        finally:
            await engine.dispose()

    asyncio.run(run())
