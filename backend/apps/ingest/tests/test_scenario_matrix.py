"""Run every scenario of the POS matrix through real ingest and reconciliation."""

import asyncio
import uuid

import pytest
from apps.controls.foundation.models import FoundationReference
from apps.ingest import reconciliation, scenarios, service
from models import Base
from models import Exception as DBException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

ALL = scenarios.scenarios()


def raised(scenario: scenarios.Scenario) -> set[str]:
    async def go():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                for code in scenario.catalog:
                    db.add(
                        FoundationReference(
                            id=str(uuid.uuid4()), category="Item", code=code, name=code, active=True
                        )
                    )
                result = await service.ingest(db, "POS", "pos", scenario.pos)
                assert result["quarantined"] == 0, result
                if scenario.processor:
                    await service.ingest(db, "Processor", "processor", scenario.processor)
                await reconciliation.reconcile(db)
                types = (await db.execute(select(DBException.exception_type))).scalars().all()
                return set(types)
        finally:
            await engine.dispose()

    return asyncio.run(go())


def test_scenario_names_are_unique():
    names = [s.name for s in ALL]
    assert len(names) == len(set(names))


@pytest.mark.parametrize("scenario", ALL, ids=[s.name for s in ALL])
def test_scenario_raises_exactly_the_expected_audit_errors(scenario):
    assert raised(scenario) & scenarios.AUDIT_ERRORS == scenario.expect


def test_matrix_covers_every_tender_type_and_every_error_rule():
    tenders = {t["tender_type"] for s in ALL for r in s.pos for t in r["tenders"]}
    assert tenders >= {"Cash", "Card", "Voucher", "GiftCard", "StoreCredit", "Other"}
    assert set().union(*(s.expect for s in ALL)) == scenarios.AUDIT_ERRORS
    assert {s.group for s in ALL} >= {"sale", "discount", "return", "edge", "exchange", "error"}
