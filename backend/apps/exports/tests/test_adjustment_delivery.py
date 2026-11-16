"""A verified adjustment is delivered to the financial system, and delivered only once."""

import asyncio
import json
from decimal import Decimal

from models import (
    Base,
    Case,
    ExportDestination,
    HumanDecision,
    Recommendation,
    ValidationObligation,
    Workflow,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.exports import routes
from apps.exports.schemas import RunIn


def test_verified_adjustment_is_delivered_once(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "EXPORT_DIR", tmp_path)

    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add_all(
                    [
                        ExportDestination(
                            id="d1", name="ERP", kind="file", format="json", config={}
                        ),
                        Case(
                            id="c1",
                            case_number="C1",
                            case_type="test",
                            status="Closed",
                            store_id="S1",
                            business_date="2026-10-01",
                            currency="USD",
                        ),
                        Recommendation(
                            id="r1",
                            case_id="c1",
                            investigation_id="i",
                            status="Approved",
                            disposition_type="Correction",
                            action_class="test",
                            expected_workflow="test",
                            financial_impact=Decimal("10"),
                            confidence="High",
                        ),
                        HumanDecision(
                            id="h1",
                            case_id="c1",
                            recommendation_id="r1",
                            decision_type="human",
                            decision="Approved",
                            actor="finance approver",
                            authority_check="Approved",
                        ),
                        Workflow(
                            id="w1",
                            case_id="c1",
                            recommendation_id="r1",
                            workflow_type="test",
                            state="Completed",
                            definition_version="1",
                            current_step="Closed",
                        ),
                        ValidationObligation(
                            id="v1",
                            case_id="c1",
                            workflow_id="w1",
                            status="Complete",
                            verification_result="Verified",
                            expected_observation="test",
                            due_window_hours=1,
                            verification_rule="test",
                        ),
                    ]
                )
                await db.commit()
                body = RunIn(destination_id="d1", dataset="adjustments")
                user = {"role": "it", "user": "it"}
                first = await routes.run_export(body, db, user)
                assert first["status"] in ("Sent", "Acknowledged"), first
                files = list(tmp_path.rglob("*.json"))
                assert files, "the batch must be written to the destination"
                assert "finance approver" in files[0].read_text()
                assert json.loads(files[0].read_text())
                second = await routes.run_export(body, db, user)
                assert second["count"] == 0, "the same adjustment is not exported twice"
        finally:
            await engine.dispose()

    asyncio.run(run())
