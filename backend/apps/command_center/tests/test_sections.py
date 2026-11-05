import asyncio

from apps.command_center.routes import section


class FakeDb:
    rolled_back = False

    async def rollback(self):
        self.rolled_back = True


def run(coro):
    return asyncio.run(coro)


async def ok(_db, _user):
    return {"fine": True}


async def boom(_db, _user):
    raise RuntimeError("bad")


def test_role_without_access_gets_null():
    assert run(section(FakeDb(), {"role": "analyst"}, ("finance",), ok)) is None


def test_scoped_user_does_not_get_system_wide_section():
    user = {"role": "finance", "store_scoped": True, "stores": ["S1"]}
    assert run(section(FakeDb(), user, ("finance",), ok, system_wide=True)) is None


def test_failing_section_reports_error_and_rolls_back():
    db = FakeDb()
    result = run(section(db, {"role": "finance"}, ("finance",), boom))
    assert "error" in result and db.rolled_back


def test_allowed_section_returns_data():
    assert run(section(FakeDb(), {"role": "finance"}, ("finance",), ok)) == {"fine": True}


def test_endpoint_builds_every_section_on_an_empty_database():
    from models import Base
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    from apps.command_center.routes import command_center

    async def go():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                user = {"role": "admin", "user": "tester", "name": "Tester"}
                return await command_center(db=db, user=user)
        finally:
            await engine.dispose()

    result = run(go())
    assert result["work"]["hotspots"] == {"stores": [], "types": []}
    assert result["work"]["queue"] == [] and result["work"]["mine"] == []
    assert result["cash"]["declarations"] == 0
    assert result["activity"]["events"] == []
    errors = {k: v for k, v in result.items() if isinstance(v, dict) and "error" in v}
    # system_health and feeds read database metadata sqlite cannot answer; everything else must work.
    assert set(errors) <= {"health", "feeds"}, errors


def test_close_counts_closed_and_blocked_days_and_lists_failed_exports():
    from datetime import UTC, datetime, timedelta

    from models import Base
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    from apps.command_center.routes import command_center
    from apps.exports.models import ExportBatch, ExportDestination
    from apps.store_days.models import StoreDay

    yesterday = (datetime.now(UTC).date() - timedelta(days=1)).isoformat()

    async def go():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add_all(
                    [
                        StoreDay(id="d1", store_id="S1", business_date=yesterday, status="Closed"),
                        StoreDay(id="d2", store_id="S2", business_date=yesterday, status="Open"),
                        ExportDestination(id="x", name="GL", kind="webhook", config={}),
                        ExportBatch(
                            id="b1",
                            destination_id="x",
                            dataset="gl",
                            status="Failed",
                            idempotency_key="k1",
                            error="HTTP 503",
                            created_by="tester",
                        ),
                    ]
                )
                await db.commit()
                return await command_center(db=db, user={"role": "admin", "user": "t"})
        finally:
            await engine.dispose()

    result = run(go())
    close = result["close"]
    assert close["business_date"] == yesterday
    assert (close["opened"], close["closed"], close["ready"], close["blocked"]) == (2, 1, 0, 1)
    assert close["blocked_examples"][0]["store_id"] == "S2"
    assert close["blocked_reasons"], close
    assert close["export"] == {"Not exported": 1}
    failed = result["exports"]["failed_batches"]
    assert [(b["id"], b["destination"], b["error"]) for b in failed] == [("b1", "GL", "HTTP 503")]
