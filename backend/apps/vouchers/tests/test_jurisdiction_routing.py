"""Routing is explainable, scoped, versioned, and frozen when Finance confirms it."""

import asyncio
import importlib.util
import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from models import Base
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.controls.foundation.models import FoundationReference
from apps.vouchers.jurisdiction_routes import (
    VoucherLocationsBody,
    get_routing_policy,
    update_routing_policy,
    update_voucher_locations,
)
from apps.vouchers.jurisdictions import RoutingPolicyInput, route_voucher
from apps.vouchers.models import Voucher
from apps.vouchers.routes import (
    EscheatBody,
    IssueBody,
    escheat_vouchers,
    issue_voucher,
    list_vouchers,
    today,
)

IT = {"role": "it", "user": "configurer"}
FINANCE = {"role": "finance", "user": "approver"}


def run_db(scenario):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                await scenario(db)
        finally:
            await engine.dispose()

    asyncio.run(run())


def policy(rules, **patch):
    return {
        "enabled": True,
        "revision": "version-one",
        "hq_location": {"country": "GB", "state": None},
        "income_partner": "RETAIL-INCOME",
        "rules": rules,
        **patch,
    }


@pytest.mark.parametrize("level", ["Store", "Issuer", "Recipient", "HQ"])
def test_hierarchy_selects_first_permitted_location_and_retains_the_path(level):
    voucher = Voucher(
        id="v",
        voucher_number="V",
        status="Issued",
        issued_store_id="S1",
        escheat_locations={
            "Store": {"country": "US", "state": "CA"},
            "Issuer": {"country": "CA", "state": "ON"},
            "Recipient": {"country": "FR", "state": None},
        },
    )
    jurisdictions = {
        "Store": ("US", "CA"),
        "Issuer": ("CA", "ON"),
        "Recipient": ("FR", None),
        "HQ": ("GB", None),
    }
    rules = [
        {"country": country, "state": state, "locations": [name] if name == level else []}
        for name, (country, state) in jurisdictions.items()
    ]
    result = route_voucher(voucher, policy(rules), {})
    assert result["destination_type"] == "Government"
    assert result["location_of"] == level
    assert result["trace"][-1]["outcome"] == "Selected"
    assert [step["location_of"] for step in result["trace"]] == list(jurisdictions)[
        : list(jurisdictions).index(level) + 1
    ]


def test_state_exclusion_overrides_country_and_exhausted_hierarchy_uses_income_partner():
    voucher = Voucher(
        status="Issued",
        issued_store_id="S1",
        escheat_locations={
            "Store": {"country": "US", "state": "CA"},
        },
    )
    result = route_voucher(
        voucher,
        policy(
            [
                {"country": "US", "state": None, "locations": ["Store"]},
                {"country": "US", "state": "CA", "locations": []},
            ]
        ),
        {},
    )
    assert result["destination_type"] == "RetailerIncome"
    assert result["income_partner"] == "RETAIL-INCOME"
    assert result["trace"][0]["outcome"] == "Location excluded by rule"
    assert len(result["trace"]) == 4


def test_policy_validation_and_optimistic_revision_guard():
    with pytest.raises(ValidationError):
        RoutingPolicyInput(enabled=True)
    with pytest.raises(ValidationError):
        RoutingPolicyInput(
            rules=[
                {"country": "us", "state": " ca ", "locations": ["Store"]},
                {"country": "US", "state": "CA", "locations": ["Issuer"]},
            ]
        )

    async def scenario(db):
        original = await get_routing_policy(db, IT)
        assert original["enabled"] is False and original["revision"] is None
        saved = await update_routing_policy(
            RoutingPolicyInput(
                enabled=True,
                income_partner="RETAIL-INCOME",
                rules=[{"country": " us ", "locations": ["Store"]}],
            ),
            db,
            IT,
        )
        assert saved["rules"][0]["country"] == "US"
        with pytest.raises(HTTPException) as stale:
            await update_routing_policy(RoutingPolicyInput(enabled=False), db, IT)
        assert stale.value.status_code == 409
        assert (await get_routing_policy(db, IT))["revision"] == saved["revision"]

    run_db(scenario)


def test_issue_store_snapshot_and_confirmation_guard_preserve_completed_routing():
    async def scenario(db):
        store = FoundationReference(
            id=str(uuid.uuid4()),
            category="Store",
            code="S1",
            name="Store",
            attributes={"country": "US", "province": "CA"},
        )
        db.add(store)
        await db.commit()
        rules = [{"country": "US", "state": "CA", "locations": ["Store"]}]
        saved = await update_routing_policy(
            RoutingPolicyInput(
                enabled=True,
                income_partner="INCOME",
                rules=rules,
            ),
            db,
            IT,
        )
        past = (date.fromisoformat(today()) - timedelta(days=5)).isoformat()
        issued = await issue_voucher(
            IssueBody(
                voucher_number="V", store_id="S1", amount="25", issued_on=past, expires_on=past
            ),
            db,
            FINANCE,
        )
        assert issued["escheat_locations"]["Store"] == {"country": "US", "state": "CA"}
        store.attributes = {"country": "GB"}
        await db.commit()
        listed = (await list_vouchers("Eligible", None, None, 100, db, FINANCE))[0]
        assert listed["routing"]["label"] == "US / CA"
        assert listed["routing"]["trace"][0]["source"] == "Voucher"
        with pytest.raises(HTTPException) as unreviewed:
            await escheat_vouchers(EscheatBody(voucher_numbers=["V"]), db, FINANCE)
        assert unreviewed.value.status_code == 409
        # Even an unchanged destination needs a fresh review after a policy revision.
        saved = await update_routing_policy(
            RoutingPolicyInput(
                enabled=True,
                income_partner="INCOME",
                rules=rules,
                expected_revision=saved["revision"],
            ),
            db,
            IT,
        )
        with pytest.raises(HTTPException) as stale:
            await escheat_vouchers(
                EscheatBody(voucher_numbers=["V"], routing_tokens={"V": listed["routing_token"]}),
                db,
                FINANCE,
            )
        assert stale.value.status_code == 409
        fresh = (await list_vouchers("Eligible", None, None, 100, db, FINANCE))[0]
        await escheat_vouchers(
            EscheatBody(voucher_numbers=["V"], routing_tokens={"V": fresh["routing_token"]}),
            db,
            FINANCE,
        )
        await update_routing_policy(
            RoutingPolicyInput(enabled=False, expected_revision=saved["revision"]), db, IT
        )
        final = (await list_vouchers("Escheated", None, None, 100, db, FINANCE))[0]
        assert final["routing"] == fresh["routing"]
        assert final["routing_token"] is None
        with pytest.raises(HTTPException) as final_edit:
            await update_voucher_locations("V", VoucherLocationsBody(locations={}), db, FINANCE)
        assert final_edit.value.status_code == 409

    run_db(scenario)


def test_jurisdiction_corrections_are_store_scoped_and_invalidate_old_previews():
    async def scenario(db):
        await update_routing_policy(
            RoutingPolicyInput(
                enabled=True,
                income_partner="INCOME",
                rules=[{"country": "CA", "locations": ["Issuer"]}],
            ),
            db,
            IT,
        )
        past = (date.fromisoformat(today()) - timedelta(days=5)).isoformat()
        await issue_voucher(
            IssueBody(
                voucher_number="V", store_id="S2", amount="25", issued_on=past, expires_on=past
            ),
            db,
            FINANCE,
        )
        initial = (await list_vouchers("Eligible", None, None, 100, db, FINANCE))[0]
        body = VoucherLocationsBody(locations={"Issuer": {"country": "ca"}}, expected_locations={})
        manager = {
            "user": "manager",
            "role": "store_manager",
            "store_scoped": True,
            "stores": ["S1"],
        }
        with pytest.raises(HTTPException) as other_store:
            await update_voucher_locations("V", body, db, manager)
        assert other_store.value.status_code == 403
        await update_voucher_locations("V", body, db, FINANCE)
        with pytest.raises(HTTPException) as stale_edit:
            await update_voucher_locations("V", body, db, FINANCE)
        assert stale_edit.value.status_code == 409
        with pytest.raises(HTTPException) as stale_preview:
            await escheat_vouchers(
                EscheatBody(voucher_numbers=["V"], routing_tokens={"V": initial["routing_token"]}),
                db,
                FINANCE,
            )
        assert stale_preview.value.status_code == 409
        fresh = (await list_vouchers("Eligible", None, None, 100, db, FINANCE))[0]
        assert fresh["routing"]["location_of"] == "Issuer"

    run_db(scenario)


def test_legacy_records_use_current_store_reference_but_completed_legacy_records_do_not_reroute():
    legacy = Voucher(status="Issued", issued_store_id="S1", escheat_locations=None)
    settings = policy([{"country": "US", "state": None, "locations": ["Store"]}])
    result = route_voucher(legacy, settings, {"S1": {"country": "US", "state": None}})
    assert result["location_of"] == "Store" and result["trace"][0]["source"] == "Store reference"
    legacy.status = "Escheated"
    assert route_voucher(legacy, settings, {})["destination_type"] == "Legacy"


def test_migration_round_trip_preserves_existing_ledger_rows():
    path = (
        Path(__file__).resolve().parents[3]
        / "alembic/versions/0111_voucher_jurisdiction_routing.py"
    )
    spec = importlib.util.spec_from_file_location("voucher_routing_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE vouchers (id VARCHAR(36) PRIMARY KEY)"))
            connection.execute(text("INSERT INTO vouchers (id) VALUES ('existing')"))
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                assert (
                    connection.execute(
                        text("SELECT escheat_routing_snapshot FROM vouchers")
                    ).scalar_one()
                    is None
                )
                migration.downgrade()
                assert (
                    connection.execute(text("SELECT id FROM vouchers")).scalar_one() == "existing"
                )
                assert "voucher_routing_policy" not in inspect(connection).get_table_names()
    finally:
        engine.dispose()


def test_routing_policy_routes_are_reachable_and_reject_non_it_writes():
    from database import get_db
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from apps.security.authentication import current_user
    from apps.vouchers.routes import router

    async def scenario(db):
        app = FastAPI()
        app.include_router(router)
        identity = dict(IT)

        async def provide_db():
            yield db

        async def provide_user():
            return identity

        app.dependency_overrides[get_db] = provide_db
        app.dependency_overrides[current_user] = provide_user
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/api/vouchers/routing-policy")).status_code == 200
            for role in ("finance", "analyst", "auditor", "store_manager", "viewer"):
                identity["role"] = role
                response = await client.put("/api/vouchers/routing-policy", json={"enabled": False})
                assert response.status_code == 403, role
                retention_response = await client.post(
                    "/api/vouchers/retention/run",
                    json={
                        "tender_type": "Voucher",
                        "preview_token": "a" * 64,
                        "confirm_purge": True,
                    },
                )
                assert retention_response.status_code == 403, role
            identity["role"] = "it"
            response = await client.put("/api/vouchers/routing-policy", json={"enabled": False})
            assert response.status_code == 200 and response.json()["revision"]

    run_db(scenario)
