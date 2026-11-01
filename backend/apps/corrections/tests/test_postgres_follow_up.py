"""Concurrency checks against an isolated PostgreSQL schema."""
import asyncio
import os
import uuid
from types import SimpleNamespace

import pytest
from integrations.ontology.nimbus import audit
from models import Base, Case
from models import Exception as CaseException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.corrections.follow_up import create_follow_up
from apps.corrections.reconciliation import linked_cases

POSTGRES_URL = os.getenv("ZETSA_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="ZETSA_TEST_POSTGRES_URL not configured")


def test_approval_discovers_follow_up_created_after_initial_lookup():
    async def run():
        schema = "zetsa_test_" + uuid.uuid4().hex
        admin = create_async_engine(POSTGRES_URL)
        engine = None
        try:
            async with admin.begin() as connection:
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            engine = create_async_engine(POSTGRES_URL, connect_args={"server_settings": {"search_path": schema}})
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with sessions() as db:
                db.add(Case(id="parent", case_number="P", status="Closed", case_type="Mismatch",
                            business_date="2026-10-01", store_id="S1", amount_needs_review=True))
                await db.flush()
                db.add(CaseException(id="exception", case_id="parent", exception_type="Mismatch",
                    exception_family="test", source_system="POS", detection_origin="test", detection_rule_id="r",
                    exception_amount=10, estimated_exposure=10, severity="High", confidence="High", close_impact="Yes"))
                audit(db, "CASE_CREATED", "parent", "Case", "parent", "created", after={"transaction_ids": ["tx"]})
                audit(db, "CASE_EVIDENCE_STALE", "parent", "TransactionAdjustment", "a1", "corrected")
                await db.commit()

            initial_lookup, continue_lookup = asyncio.Event(), asyncio.Event()
            async with sessions() as approval, sessions() as follow_up:
                execute = approval.execute

                async def pause_after_lookup(statement, *args, **kwargs):
                    result = await execute(statement, *args, **kwargs)
                    if "FROM audit_events JOIN cases" in str(statement) and not initial_lookup.is_set():
                        initial_lookup.set()
                        await asyncio.wait_for(continue_lookup.wait(), timeout=5)
                    return result

                approval.execute = pause_after_lookup
                task = asyncio.create_task(linked_cases(approval, SimpleNamespace(id="tx", store_id="S1")))
                await asyncio.wait_for(initial_lookup.wait(), timeout=5)
                child = await create_follow_up(follow_up, "parent", {"user": "finance"})
                continue_lookup.set()
                cases = await asyncio.wait_for(task, timeout=5)
                assert [case.id for case in cases] == ["parent", child.id]
                await approval.rollback()
        finally:
            if engine:
                await engine.dispose()
            async with admin.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await admin.dispose()
    asyncio.run(run())
