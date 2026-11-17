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


@pytest.fixture(autouse=True)
def install_controls(monkeypatch):
    """Stand in for an approved Ontology Studio release.

    Audit rules exist only in the ontology, so every test starts with a release that defines the policy
    limits and no rules. A test installs rules with ``install_controls(total_rules=[...])`` or the
    unavailable case with ``install_controls(None)``.
    """
    from decimal import Decimal

    from integrations.ontology import audit_controls as ac
    from integrations.ontology.ontology_integration import integration

    state: dict = {}

    def install(
        controls="default",
        *,
        total_rules=(),
        transaction_rules=(),
        autonomy="1000",
        timing="250",
        currency="USD",
    ):
        state["controls"] = (
            None
            if controls is None
            else ac.AuditControls(
                release_id=1,
                total_rules=tuple(total_rules),
                transaction_rules=tuple(transaction_rules),
                limits={
                    "autonomy_limit": Decimal(autonomy),
                    "timing_autonomy_limit": Decimal(timing),
                },
                limit_currency=currency,
            )
        )

    def audit_controls():
        if state["controls"] is None:
            raise ac.AuditRulesUnavailable("No approved ontology release has been loaded")
        return state["controls"]

    monkeypatch.setattr(integration, "audit_controls", audit_controls)
    install()
    return install
