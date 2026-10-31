import asyncio

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.corrections.follow_up import create_follow_up
from apps.corrections.follow_up import load_follow_up_state, parent_lineage_events
from apps.corrections.reconciliation import linked_cases
from apps.exports.routes import build, tracking_page
from integrations.ontology.nimbus import audit
from models import AuditEvent, Base, Case, Exception as CaseException, ExportDestination


def test_closed_case_follow_up_is_scoped_idempotent_and_preserves_history():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                parent = Case(id="parent", case_number="P", status="Closed", case_type="Mismatch",
                              business_date="2026-10-01", store_id="S1", currency="USD",
                              amount_needs_review=True, total_exception_amount=10, total_exposure=10)
                db.add(parent)
                db.add(CaseException(id="exception", case_id="parent", exception_type="Mismatch",
                    exception_family="test", source_system="POS", detection_origin="test", detection_rule_id="r",
                    exception_amount=10, estimated_exposure=10, severity="High", confidence="High", close_impact="Yes"))
                audit(db, "CASE_CREATED", "parent", "Case", "parent", "created", after={"transaction_ids": ["tx"]})
                audit(db, "CASE_EVIDENCE_STALE", "parent", "TransactionAdjustment", "a1", "corrected")
                await db.commit()
                with pytest.raises(HTTPException) as error:
                    await create_follow_up(db, "parent", {"user": "finance", "store_scoped": True, "stores": ["S2"]})
                assert error.value.status_code == 403
                child = await create_follow_up(db, "parent", {"user": "finance"})
                assert child.id != parent.id and child.amount_needs_review
                assert parent.status == "Closed" and parent.total_exception_amount == 10
                copied = (await db.execute(select(CaseException).where(CaseException.case_id == child.id))).scalar_one()
                assert copied.id != "exception" and copied.exception_amount == 10
                created = (await db.execute(select(AuditEvent).where(AuditEvent.case_id == child.id,
                           AuditEvent.event_type == "CASE_CREATED"))).scalar_one()
                assert created.after_state["transaction_ids"] == ["tx"]
                assert (await create_follow_up(db, "parent", {"user": "finance"})).id == child.id
                child.amount_needs_review = False
                audit(db, "CASE_EVIDENCE_STALE", "parent", "TransactionAdjustment", "a2", "corrected again")
                await db.commit()
                assert (await create_follow_up(db, "parent", {"user": "finance"})).id == child.id
                assert child.amount_needs_review
                # A later correction invalidates both cases in the approval transaction.
                # The follow-up may be reviewed and closed without reopening the parent.
                audit(db, "CASE_EVIDENCE_STALE", "parent", "TransactionAdjustment", "a-direct", "corrected")
                audit(db, "CASE_EVIDENCE_STALE", child.id, "TransactionAdjustment", "a-direct", "corrected",
                      after={"case_closed": False})
                child.status = "Closed"
                await db.commit()
                with pytest.raises(HTTPException, match="No new correction"):
                    await create_follow_up(db, "parent", {"user": "finance"})
                audit(db, "CASE_EVIDENCE_STALE", "parent", "TransactionAdjustment", "a3", "corrected again")
                audit(db, "CASE_EVIDENCE_STALE", child.id, "TransactionAdjustment", "a3", "corrected again",
                      after={"case_closed": True})
                await db.commit()
                replacement = await create_follow_up(db, child.id, {"user": "finance"})
                assert replacement.id != child.id
                assert (await create_follow_up(db, "parent", {"user": "finance"})).id == replacement.id
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_follow_up_state_queries_large_child_history_in_database():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add(Case(id="parent", case_number="P", status="Closed", case_type="test",
                            business_date="2026-10-01", store_id="S1"))
                for index in range(1_100):
                    child_id = f"child-{index}"
                    db.add(Case(id=child_id, case_number=child_id,
                                status="Open" if index == 1_099 else "Closed", case_type="test",
                                business_date="2026-10-01", store_id="S1"))
                    audit(db, "CASE_FOLLOW_UP_LINKED", "parent", "Case", child_id, "linked",
                          after={"follow_up_id": child_id, "stale_event_ids": []})
                db.add(AuditEvent(id="legacy-follow-up-link", event_type="CASE_FOLLOW_UP_LINKED",
                    actor="system", object_type="Case", object_id="parent", case_id="parent",
                    action_description="Malformed legacy follow-up metadata", after_state=["legacy"]))
                await db.commit()

                events = await parent_lineage_events(db, "parent")
                state = await load_follow_up_state(db, "parent", events)
                assert state.pending_event_ids == []
                assert [child.id for child in state.children] == ["child-1099"]
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_malformed_parent_creation_metadata_does_not_break_follow_up():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add(Case(id="parent", case_number="P", status="Closed", case_type="Mismatch",
                            business_date="2026-10-01", store_id="S1", currency="USD",
                            amount_needs_review=True, total_exception_amount=10, total_exposure=10))
                db.add(CaseException(id="exception", case_id="parent", exception_type="Mismatch",
                    exception_family="test", source_system="POS", detection_origin="test", detection_rule_id="r",
                    exception_amount=10, estimated_exposure=10, severity="High", confidence="High", close_impact="Yes"))
                db.add(AuditEvent(id="legacy-created", event_type="CASE_CREATED", actor="system",
                    object_type="Case", object_id="parent", case_id="parent", action_description="legacy",
                    after_state=["unexpected"]))
                audit(db, "CASE_EVIDENCE_STALE", "parent", "TransactionAdjustment", "a1", "corrected")
                await db.commit()

                child = await create_follow_up(db, "parent", {"user": "finance"})
                created = (await db.execute(select(AuditEvent).where(
                    AuditEvent.case_id == child.id, AuditEvent.event_type == "CASE_CREATED"
                ))).scalar_one()
                assert created.after_state["transaction_ids"] == []
                assert created.after_state["parent_case_id"] == "parent"
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_linked_cases_lock_parent_before_follow_up_even_when_ids_sort_oppositely():
    async def run():
        from types import SimpleNamespace

        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                for case_id in ("z-parent", "a-child", "m-malformed-child"):
                    db.add(Case(id=case_id, case_number=case_id, status="Closed", case_type="test",
                                business_date="2026-10-01", store_id="S1"))
                await db.flush()
                audit(db, "CASE_CREATED", "z-parent", "Case", "z-parent", "created",
                      after={"transaction_ids": ["tx"]})
                audit(db, "CASE_CREATED", "a-child", "Case", "a-child", "created",
                      after={"transaction_ids": ["tx"], "parent_case_id": "z-parent"})
                audit(db, "CASE_CREATED", "m-malformed-child", "Case", "m-malformed-child", "legacy",
                      after={"transaction_ids": ["tx"], "parent_case_id": {"legacy": "invalid"}})
                await db.commit()
                cases = await linked_cases(db, SimpleNamespace(id="tx", store_id="S1"))
                assert [case.id for case in cases] == ["z-parent", "a-child"]
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_stale_closed_cases_do_not_block_unrelated_exports(monkeypatch):
    from apps.exports import routes
    monkeypatch.setattr(routes, "MAX_BATCH", 1)

    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                dest = ExportDestination(id="dest", name="ERP", kind="file", format="json", config={})
                db.add(dest)
                for case_id, stale in (("stale", True), ("valid", False)):
                    db.add(Case(id=case_id, case_number=case_id, status="Closed", case_type="test",
                                business_date="2026-10-01", store_id="S1", currency="USD", amount_needs_review=stale))
                await db.commit()
                records, items, _, _ = await build(db, dest, "resolutions")
                assert [item[0] for item in items] == ["valid"]
                assert records[0]["case_number"] == "valid"
                _, tracking, total = await tracking_page(db, dest.id, "resolutions", "", "", 1, 10)
                rows = {row["object_id"]: row for row in tracking}
                assert total == 2
                assert rows["stale"]["state"] == "not_eligible"
                assert "follow-up" in rows["stale"]["reason"]
                assert rows["valid"]["state"] == "waiting"
        finally:
            await engine.dispose()
    asyncio.run(run())
