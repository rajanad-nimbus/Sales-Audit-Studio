import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.exports import routes
from models import AuditEvent, Base, Case, ExportBatch, ExportDestination, ExportItem, HumanDecision, Recommendation, ValidationObligation, Workflow


def test_export_selection_skips_reserved_and_ineligible_cases_before_limit(monkeypatch):
    monkeypatch.setattr(routes, "MAX_BATCH", 1)

    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session = async_sessionmaker(engine, expire_on_commit=False)
            async with session() as db:
                destination = ExportDestination(id="d1", name="ERP", kind="file", format="json", config={})
                db.add(destination)
                date = datetime(2026, 10, 1, tzinfo=timezone.utc)
                for index, case_id in enumerate(("reserved", "ineligible", "eligible")):
                    db.add(Case(id=case_id, case_number=case_id, case_type="test", status="Closed",
                                business_date="2026-10-01", store_id="S1", currency="EUR", updated_at=date + timedelta(hours=index)))
                db.add(ExportBatch(id="b1", destination_id="d1", dataset="adjustments", status="Sent",
                                   idempotency_key="k1", created_by="it"))
                db.add(ExportItem(id="e1", batch_id="b1", destination_id="d1", dataset="adjustments",
                                  object_id="reserved", object_version="v1", state="Delivered"))
                db.add(Recommendation(id="r1", case_id="eligible", investigation_id="i", status="Approved",
                                      disposition_type="Correction", action_class="test", expected_workflow="test",
                                      financial_impact=Decimal("10"), confidence="High"))
                db.add(HumanDecision(id="h1", case_id="eligible", recommendation_id="r1", decision_type="human",
                                     decision="Approved", actor="approver", authority_check="Approved"))
                db.add(Workflow(id="w1", case_id="eligible", recommendation_id="r1", workflow_type="test",
                                state="Completed", definition_version="1", current_step="Closed"))
                db.add(ValidationObligation(id="v1", case_id="eligible", workflow_id="w1", status="Complete",
                                             verification_result="Verified", expected_observation="test",
                                             due_window_hours=1, verification_rule="test"))
                await db.commit()

                records, items, _, _ = await routes.build(db, destination, "adjustments")
                assert [item[0] for item in items] == ["eligible"]
                assert [record["amount"] for record in records] == ["10.00"]
                assert records[0]["approved_by"] == "approver"
                assert records[0]["currency"] == "EUR"
                case = await db.get(Case, "eligible")
                case.currency = None
                await db.commit()
                with pytest.raises(HTTPException, match="no currency"):
                    await routes.build(db, destination, "adjustments")
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_adjustment_requires_matching_approved_verified_workflow():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session = async_sessionmaker(engine, expire_on_commit=False)
            async with session() as db:
                destination = ExportDestination(id="d1", name="ERP", kind="file", format="json", config={})
                db.add(destination)
                db.add(Case(id="c1", case_number="C1", case_type="test", status="Closed", store_id="S1",
                            business_date="2026-10-01", currency="USD"))
                for rec_id, amount in (("r1", "10"), ("r2", "0")):
                    db.add(Recommendation(id=rec_id, case_id="c1", investigation_id="i", status="Approved",
                                          disposition_type="Correction", action_class="test", expected_workflow="test",
                                          financial_impact=Decimal(amount), confidence="High"))
                db.add(HumanDecision(id="h1", case_id="c1", recommendation_id="r1", decision_type="human",
                                     decision="Approved", actor="correct approver", authority_check="Approved"))
                db.add(Workflow(id="w2", case_id="c1", recommendation_id="r2", workflow_type="test",
                                state="Completed", definition_version="1", current_step="Closed"))
                db.add(ValidationObligation(id="v2", case_id="c1", workflow_id="w2", status="Complete",
                                             verification_result="Verified", expected_observation="test",
                                             due_window_hours=1, verification_rule="test"))
                await db.commit()
                assert (await routes.build(db, destination, "adjustments"))[0] == []

                db.add(Workflow(id="w1", case_id="c1", recommendation_id="r1", workflow_type="test",
                                state="Completed", definition_version="1", current_step="Closed"))
                validation = ValidationObligation(id="v1", case_id="c1", workflow_id="w1", status="Complete",
                                                  verification_result="Failed", expected_observation="test",
                                                  due_window_hours=1, verification_rule="test")
                db.add(validation)
                await db.commit()
                assert (await routes.build(db, destination, "adjustments"))[0] == []
                validation.verification_result = "Verified"
                await db.commit()
                records, _, _, _ = await routes.build(db, destination, "adjustments")
                assert len(records) == 1
                assert records[0]["approved_by"] == "correct approver"
                assert records[0]["amount"] == "10.00"
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_closed_case_context_selects_latest_records_per_case():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session = async_sessionmaker(engine, expire_on_commit=False)
            async with session() as db:
                case = Case(id="c1", case_number="C1", case_type="test", status="Closed",
                            store_id="S1", business_date="2026-10-01")
                db.add(case)
                for identifier, created_at in (("old", datetime(2026, 10, 1, tzinfo=timezone.utc)),
                                               ("new", datetime(2026, 10, 2, tzinfo=timezone.utc))):
                    db.add(Recommendation(
                        id=f"r-{identifier}", case_id=case.id, investigation_id="i", status="Approved",
                        disposition_type=identifier, action_class="test", expected_workflow="test",
                        financial_impact=Decimal("1"), confidence="High", created_at=created_at,
                    ))
                    db.add(HumanDecision(
                        id=f"h-{identifier}", case_id=case.id, recommendation_id=f"r-{identifier}",
                        decision_type="human", decision="Approved", actor=identifier,
                        authority_check="Approved", created_at=created_at,
                    ))
                    db.add(Workflow(
                        id=f"w-{identifier}", case_id=case.id, recommendation_id=f"r-{identifier}",
                        workflow_type="test", state="Completed", definition_version="1", current_step="Closed",
                        created_at=created_at,
                    ))
                    db.add(ValidationObligation(
                        id=f"v-{identifier}", case_id=case.id, workflow_id=f"w-{identifier}",
                        status="Complete", verification_result=identifier, expected_observation="test",
                        due_window_hours=1, verification_rule="test", created_at=created_at,
                    ))
                db.add(Recommendation(
                    id="r-unvalidated", case_id=case.id, investigation_id="i", status="Approved",
                    disposition_type="unvalidated", action_class="test", expected_workflow="test",
                    financial_impact=Decimal("1"), confidence="High",
                    created_at=datetime(2026, 10, 3, tzinfo=timezone.utc),
                ))
                db.add(HumanDecision(
                    id="h-unvalidated", case_id=case.id, recommendation_id="r-unvalidated",
                    decision_type="human", decision="Approved", actor="unvalidated",
                    authority_check="Approved", created_at=datetime(2026, 10, 3, tzinfo=timezone.utc),
                ))
                for seq, event_hash in ((1, "old-hash"), (2, "new-hash")):
                    db.add(AuditEvent(
                        id=f"event-{seq}", event_type="CASE_UPDATED", actor="test", object_type="Case",
                        object_id=case.id, case_id=case.id, action_description="updated", seq=seq,
                        hash=event_hash,
                    ))
                await db.commit()

                context = await routes.closed_case_context(db, [case])

                assert context["recs"][case.id].id == "r-new"
                assert context["decs"][case.id].id == "h-new"
                assert context["vals"][case.id].id == "v-new"
                assert context["decs"][case.id].recommendation_id == context["recs"][case.id].id
                workflow = await db.get(Workflow, context["vals"][case.id].workflow_id)
                assert workflow.recommendation_id == context["recs"][case.id].id
                assert context["refs"][case.id] == "new-hash"
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_batch_insert_conflict_returns_conflict_without_delivering(monkeypatch):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session = async_sessionmaker(engine, expire_on_commit=False)
            async with session() as db:
                db.add(ExportDestination(id="d1", name="ERP", kind="file", format="json", config={}))
                db.add(Case(id="c1", case_number="C1", case_type="test", status="Closed", store_id="S1",
                            business_date="2026-10-01", updated_at=datetime(2026, 10, 1, tzinfo=timezone.utc)))
                await db.commit()
                delivered = []

                async def fail_flush(*_args, **_kwargs):
                    raise IntegrityError(
                        "insert", {}, Exception("UNIQUE constraint failed: export_batches.idempotency_key")
                    )

                async def deliver(*_args):
                    delivered.append(True)

                monkeypatch.setattr(db, "flush", fail_flush)
                monkeypatch.setattr(routes, "deliver", deliver)
                with pytest.raises(HTTPException) as error:
                    await routes.run_export(routes.RunIn(destination_id="d1", dataset="resolutions"), db,
                                            {"user": "it", "role": "it"})
                assert error.value.status_code == 409
                assert not delivered
                assert (await db.execute(select(ExportBatch))).scalars().all() == []
        finally:
            await engine.dispose()

    asyncio.run(run())


@pytest.mark.parametrize("scope", ["current", "backposts"])
def test_scoped_export_applies_filter_before_batch_limit(monkeypatch, scope):
    monkeypatch.setattr(routes, "MAX_BATCH", 1)

    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session = async_sessionmaker(engine, expire_on_commit=False)
            async with session() as db:
                destination = ExportDestination(id="d1", name="ERP", kind="file", format="json", config={})
                db.add(destination)
                first_date = "2026-09-30" if scope == "current" else "2026-10-01"
                last_date = "2026-10-02" if scope == "current" else "2026-10-01"
                db.add_all([
                    Case(id="first", case_number="FIRST", case_type="test", status="Closed", store_id="S1",
                         business_date=first_date, updated_at=datetime(2026, 10, 1, tzinfo=timezone.utc)),
                    Case(id="target", case_number="TARGET", case_type="test", status="Closed", store_id="S2",
                         business_date=last_date, updated_at=datetime(2026, 10, 2, tzinfo=timezone.utc)),
                ])
                await db.commit()
                records, items, _, _ = await routes.build(db, destination, "resolutions", scope)
                assert [item[0] for item in items] == ["target"]
                assert records[0]["backpost"] is (scope == "backposts")
                preview = await routes.run_export(routes.RunIn(destination_id="d1", dataset="resolutions",
                                                               dry_run=True, scope=scope), db,
                                                  {"user": "it", "role": "it"})
                assert preview["count"] == 1
                assert preview["sample"][0]["case_number"] == "TARGET"
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_previously_exported_day_is_treated_as_backpost(monkeypatch):
    monkeypatch.setattr(routes, "MAX_BATCH", 1)

    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session = async_sessionmaker(engine, expire_on_commit=False)
            async with session() as db:
                destination = ExportDestination(id="d1", name="ERP", kind="file", format="json", config={})
                db.add(destination)
                for case_id in ("sent", "later"):
                    db.add(Case(id=case_id, case_number=case_id, case_type="test", status="Closed", store_id="S1",
                                business_date="2026-10-01", updated_at=datetime(2026, 10, 1, tzinfo=timezone.utc)))
                db.add(ExportBatch(id="batch-001", destination_id="d1", dataset="resolutions", status="Sent",
                                   idempotency_key="k1", created_by="it"))
                db.add(ExportItem(id="e1", batch_id="batch-001", destination_id="d1", dataset="resolutions",
                                  object_id="sent", object_version="v1", state="Delivered"))
                await db.commit()
                records, items, _, _ = await routes.build(db, destination, "resolutions", "backposts")
                assert [item[0] for item in items] == ["later"]
                assert records[0]["backpost_reason"].startswith("Late addition")
                assert (await routes.build(db, destination, "resolutions", "current"))[0] == []
        finally:
            await engine.dispose()

    asyncio.run(run())
