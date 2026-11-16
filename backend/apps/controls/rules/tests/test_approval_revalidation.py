"""A rule is checked again when it is approved, because the totals it targets can change after drafting."""

import asyncio
from decimal import Decimal

import pytest
from apps.controls.rules.routes import RuleBody, approve_rule, create_rule, submit_rule
from fastapi import HTTPException
from models import AuditTotalDefinition, Base, ConfiguredAuditRule
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

FINANCE = {"user": "finance", "role": "finance"}


def test_a_rule_whose_total_stopped_being_a_count_cannot_be_approved():
    async def go():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add(
                    AuditTotalDefinition(
                        id="count", name="Count total", aggregation="Count", active=True
                    )
                )
                await db.commit()
                drafted = await create_rule(
                    RuleBody(
                        name="Count threshold",
                        total_name="Configured: Count total",
                        threshold=Decimal("0"),
                        threshold_currency=None,
                    ),
                    db,
                    FINANCE,
                )
                await submit_rule(drafted["id"], db, FINANCE)

                (await db.get(AuditTotalDefinition, "count")).aggregation = "Sum"
                await db.commit()
                with pytest.raises(HTTPException) as refused:
                    await approve_rule(drafted["id"], db, {"user": "approver"})
                assert refused.value.status_code == 422
                rule = await db.get(ConfiguredAuditRule, drafted["id"])
                assert (rule.status, rule.enabled) == ("Submitted", False)
        finally:
            await engine.dispose()

    asyncio.run(go())
