import asyncio
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.agents import action_center
from integrations.ontology import nimbus
from main import override_evidence_request
from models import Base, Case, EvidenceRequest, Recommendation
from schemas import EvidenceOverride


@pytest.mark.parametrize("currency,outcome", [("USD", "Permit"), ("EUR", "RequireHuman"), (None, "RequireHuman")])
def test_timing_autonomy_requires_threshold_currency(currency, outcome):
    db = SimpleNamespace(add=lambda item: None)
    case = SimpleNamespace(id="c", evidence_completeness=100, total_exception_amount=Decimal("100"), currency=currency)
    recommendation = SimpleNamespace(id="r", disposition_type="Timing", financial_impact=Decimal("0"))
    evaluation = nimbus.evaluate_policy(db, case, recommendation)
    assert evaluation.outcome == outcome
    if currency != "USD":
        assert "USD" in evaluation.reasons["reasons"][0]


def test_non_usd_bulk_financial_approval_requires_individual_review():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add(Case(id="case", case_number="CASE", case_type="test", store_id="S1", business_date="2026-10-01",
                            status="In Review", currency="EUR", evidence_completeness=100))
                await db.flush()
                db.add(Recommendation(id="rec", case_id="case", investigation_id="i", disposition_type="Correction",
                    action_class="test", expected_workflow="test", financial_impact=Decimal("1"), confidence="High", status="Proposed"))
                await db.commit()
                result = await action_center.bulk(action_center.BulkRequest(action="approve", ids=["case"]), db,
                                                  {"user": "finance", "role": "finance"})
                assert result["ok"] == 0 and "USD" in result["skipped"][0]["reason"]
                rec = await db.get(Recommendation, "rec")
                assert rec.status == "Proposed"
                (await db.get(Case, "case")).currency = "USD"
                rec.financial_impact = Decimal("-5001")
                await db.commit()
                result = await action_center.bulk(action_center.BulkRequest(action="approve", ids=["case"]), db,
                                                  {"user": "finance", "role": "finance"})
                assert result["ok"] == 0 and "above bulk limit" in result["skipped"][0]["reason"]
        finally:
            await engine.dispose()
    asyncio.run(run())


@pytest.mark.parametrize("currency,requires_second", [("USD", False), ("EUR", True), (None, True)])
def test_evidence_override_threshold_uses_currency(currency, requires_second):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add(Case(id="case", case_number="CASE", case_type="test", store_id="S1",
                            business_date="2026-10-01", currency=currency))
                await db.flush()
                db.add(EvidenceRequest(id="req", case_id="case", requirement="evidence", requested_from="person",
                                       requested_by="reviewer", status="Open"))
                await db.commit()
                with pytest.raises(HTTPException) as exc:
                    await override_evidence_request("case", "req", EvidenceOverride(rationale="verified", financial_impact=Decimal("1")),
                        db, {"user": "other", "role": "finance", "store_scoped": True, "stores": ["S2"]})
                assert exc.value.status_code == 403
                result = await override_evidence_request("case", "req", EvidenceOverride(rationale="verified", financial_impact=Decimal("1")),
                                                         db, {"user": "finance", "role": "finance"})
                assert result["requires_second_approver"] is requires_second
        finally:
            await engine.dispose()
    asyncio.run(run())
