import asyncio

import pytest
from fastapi import HTTPException
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import main
from models import AuditEvent, Base, Case
from schemas import CaseCreate, CaseUpdate


USER = {"user": "finance@example.test", "role": "finance"}


async def perform_case_change(operation, db):
    if operation == "create":
        return await main.create_case(
            CaseCreate(case_type="test", business_date="2026-10-01", store_id="S1"),
            db,
            USER,
        )
    return await main.update_case("c1", CaseUpdate(assigned_to="analyst"), db, USER)


@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize("audit_fails", [False, True])
def test_case_change_and_audit_commit_together(operation, audit_fails):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            session = async_sessionmaker(engine, expire_on_commit=False)
            async with session() as db:
                if operation == "update":
                    db.add(Case(id="c1", case_number="CASE-1", case_type="test",
                                business_date="2026-10-01", store_id="S1"))
                    await db.commit()

                def fail_audit(_mapper, _connection, _target):
                    raise RuntimeError("audit unavailable")

                if audit_fails:
                    event.listen(AuditEvent, "before_insert", fail_audit)
                try:
                    if audit_fails:
                        with pytest.raises(RuntimeError, match="audit unavailable"):
                            await perform_case_change(operation, db)
                        await db.rollback()
                    else:
                        await perform_case_change(operation, db)
                finally:
                    if audit_fails:
                        event.remove(AuditEvent, "before_insert", fail_audit)

                cases = (await db.execute(select(Case))).scalars().all()
                audits = (await db.execute(select(AuditEvent))).scalars().all()
                if operation == "create":
                    assert len(cases) == (0 if audit_fails else 1)
                else:
                    assert cases[0].assigned_to == (None if audit_fails else "analyst")
                assert len(audits) == (0 if audit_fails else 1)
                if audits:
                    assert audits[0].actor == USER["user"]
                    assert audits[0].event_type == ("CASE_CREATED" if operation == "create" else "CASE_UPDATED")
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_invalid_create_priority_is_rejected_before_database_use():
    async def run():
        with pytest.raises(HTTPException) as error:
            await main.create_case(
                CaseCreate(case_type="test", business_date="2026-10-01", store_id="S1", priority="invalid"),
                None,
                USER,
            )
        assert error.value.status_code == 422

    asyncio.run(run())


@pytest.mark.parametrize("update", [{"status": None}, {"priority": None}])
def test_null_controlled_case_fields_are_rejected(update):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            session = async_sessionmaker(engine, expire_on_commit=False)
            async with session() as db:
                db.add(Case(id="c1", case_number="CASE-1", case_type="test",
                            business_date="2026-10-01", store_id="S1"))
                await db.commit()
                with pytest.raises(HTTPException) as error:
                    await main.update_case("c1", CaseUpdate(**update), db, USER)
                assert error.value.status_code == 422
        finally:
            await engine.dispose()

    asyncio.run(run())
