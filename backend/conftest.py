"""Shared test fixtures."""

import asyncio

import pytest
from models import Base
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

_engine = None


def _middleware_sessions():
    """One in-memory database for every test that sends a request through the authentication middleware.

    The middleware records verified identities and reads role assignments, so a request with no database
    behind it cannot be authorized. Tests that need their own data still replace it with a monkeypatch.
    """
    global _engine
    if _engine is None:
        _engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)

        async def create():
            async with _engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)

        asyncio.run(create())
    return async_sessionmaker(_engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
def middleware_database(monkeypatch):
    from apps.security import middleware

    monkeypatch.setattr(middleware, "async_session", _middleware_sessions())
    yield
    if _engine is not None:
        from apps.audit.models import AuditEvent
        from apps.security.models import StoreAssignment, UserRoleAssignment, VerifiedStoreUser

        async def clean():
            async with async_sessionmaker(_engine)() as db:
                for model in (UserRoleAssignment, VerifiedStoreUser, StoreAssignment, AuditEvent):
                    await db.execute(delete(model))
                await db.commit()

        asyncio.run(clean())
