"""A decision is recorded under the signed-in user, whatever the client says."""

import asyncio

import pytest
from apps.cases.records import create_decision
from apps.cases.schemas import HumanDecisionCreate
from fastapi import HTTPException
from models import AuditEvent, Base, Case, HumanDecision, Recommendation
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

USER = {"user": "real.finance", "role": "finance"}


def run_with_db(scenario):
    async def go():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                for index in (1, 2):
                    db.add(
                        Case(
                            id=f"c{index}",
                            case_number=f"C{index}",
                            case_type="X",
                            business_date="2026-10-01",
                            store_id="S1",
                            status="In Review",
                        )
                    )
                await db.flush()
                for index in (1, 2):
                    db.add(
                        Recommendation(
                            id=f"r{index}",
                            case_id=f"c{index}",
                            investigation_id="i",
                            disposition_type="Adjust",
                            action_class="Financial",
                            expected_workflow="Post an adjustment",
                            confidence="High",
                        )
                    )
                await db.commit()
                await scenario(db)
        finally:
            await engine.dispose()

    asyncio.run(go())


def body(case: str, rec: str, **over):
    return HumanDecisionCreate(
        case_id=case,
        recommendation_id=rec,
        decision_type="Finance",
        decision="Rejected",
        rationale="Not a real loss",
        **over,
    )


def test_a_client_need_not_name_the_actor():
    async def scenario(db):
        saved = await create_decision(body("c1", "r1"), db, USER)
        assert saved.actor == "real.finance"

    run_with_db(scenario)


def test_an_actor_the_client_sends_is_ignored_and_the_audit_trail_names_the_real_user():
    async def scenario(db):
        saved = await create_decision(
            body("c2", "r2", actor="someone.else", authority_check="Passed"), db, USER
        )
        assert saved.actor == "real.finance"
        assert saved.authority_check == "Pending"  # a client cannot mark its own check as passed
        stored = (await db.execute(select(HumanDecision))).scalar_one()
        assert stored.actor == "real.finance"
        event = (
            await db.execute(select(AuditEvent).where(AuditEvent.event_type == "HUMAN_DECISION"))
        ).scalar_one()
        assert event.actor == "real.finance" and "someone.else" not in event.action_description

    run_with_db(scenario)


def test_a_decision_still_needs_a_rationale():
    async def scenario(db):
        with pytest.raises(HTTPException) as refused:
            await create_decision(
                HumanDecisionCreate(
                    case_id="c1",
                    recommendation_id="r1",
                    decision_type="Finance",
                    decision="Rejected",
                ),
                db,
                USER,
            )
        assert refused.value.status_code == 422

    run_with_db(scenario)
