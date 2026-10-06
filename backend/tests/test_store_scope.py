"""Row-level security by store: assignments come from Ontology Studio, Nimbus enforces them and fails closed."""
import asyncio
import json
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import auth
import routes_cash as cash
import routes_store_days as sd
import scope
from models import Base, CashControl, CashDeposit, StoreDay

D = Decimal


def mgr(stores):
    return {"user": "m@x", "role": "store_manager", "store_scoped": True, "stores": stores}


FINANCE = {"user": "f@x", "role": "finance", "store_scoped": False, "stores": None}


# ---- reading the assignment from the identity ---------------------------------------------------------------------
@pytest.mark.parametrize("identity,claim,expected", [
    ({"stores": ["S1", "S2"]}, None, ["S1", "S2"]),
    ({"stores": "S1, S2 ,S1,,"}, None, ["S1", "S2"]),                         # comma string, trimmed, de-duplicated
    ({"attributes": {"store_codes": ["S9"]}}, "attributes.store_codes", ["S9"]),   # dotted path
    ({}, None, []), ({"stores": None}, None, []), ({"stores": 12}, None, []), ({"stores": {"a": 1}}, None, []),
])
def test_stores_from_identity_is_strict_and_fails_closed(identity, claim, expected):
    assert auth.stores_from_identity(identity, claim) == expected


# ---- identity -> scope on the user -------------------------------------------------------------------------------
def _identity(monkeypatch, user, headers=None):
    from starlette.requests import Request

    class Resp:
        status_code = 200
        def json(self): return {"user": user}

    class Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return Resp()
    monkeypatch.setattr(auth, "ONTOLOGY_AUTH_URL", "http://ontology/auth/me")
    monkeypatch.setattr(auth.httpx, "AsyncClient", Client)
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    request = Request({"type": "http", "method": "GET", "path": "/api/me", "headers": raw, "query_string": b""})
    return asyncio.run(auth.current_user(request, "Bearer t"))


def test_a_real_store_manager_gets_the_stores_ontology_assigns(monkeypatch):
    u = _identity(monkeypatch, {"email": "a@x", "role": "store_manager", "stores": ["STORE-007", "STORE-014"]})
    assert u["role"] == "store_manager" and u["store_scoped"] and u["stores"] == ["STORE-007", "STORE-014"]
    assert scope.allowed_stores(u) == ["STORE-007", "STORE-014"]


def test_a_store_manager_with_no_assignment_sees_nothing(monkeypatch):
    u = _identity(monkeypatch, {"email": "a@x", "role": "store_manager"})
    assert u["store_scoped"] and u["stores"] == [] and not scope.store_allowed(u, "STORE-007")


def test_other_roles_are_unrestricted_even_if_a_claim_is_present(monkeypatch):
    u = _identity(monkeypatch, {"email": "f@x", "role": "finance", "stores": ["S1"]})
    assert not u["store_scoped"] and u["stores"] is None and scope.store_allowed(u, "ANY")


def test_only_a_real_admin_can_simulate_stores_and_none_given_means_none(monkeypatch):
    admin = {"email": "root@x", "role": "admin"}
    u = _identity(monkeypatch, admin, {"x-impersonate-role": "store_manager", "x-impersonate-stores": "S1, S2"})
    assert u["impersonating"] and u["stores"] == ["S1", "S2"]
    assert _identity(monkeypatch, admin, {"x-impersonate-role": "store_manager"})["stores"] == []          # fail closed
    assert _identity(monkeypatch, admin)["stores"] is None                                                   # a plain admin is unrestricted
    # A non-admin cannot widen their own scope with the header.
    u = _identity(monkeypatch, {"email": "a@x", "role": "store_manager", "stores": ["S1"]}, {"x-impersonate-stores": "S1,S2,S3"})
    assert u["stores"] == ["S1"]


# ---- helpers ----------------------------------------------------------------------------------------------------
def test_restrict_and_require():
    q = select(StoreDay)
    assert "false" in str(scope.restrict(q, mgr([]), StoreDay.store_id)).lower() or "0 = 1" in str(scope.restrict(q, mgr([]), StoreDay.store_id))
    assert "IN" in str(scope.restrict(q, mgr(["S1"]), StoreDay.store_id)) and scope.restrict(q, FINANCE, StoreDay.store_id) is q
    scope.require_store(FINANCE, "ANY")
    with pytest.raises(HTTPException) as e:
        scope.require_store(mgr(["S1"]), "S2")
    assert e.value.status_code == 403 and "S2" in e.value.detail
    assert scope.path_store("/api/store-days/S1/2026-10-01/close") == "S1" and scope.path_store("/api/store-days/stores") is None
    assert scope.path_store("/api/store-days") is None


# ---- endpoints ----------------------------------------------------------------------------------------------------
async def session():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


def test_store_days_are_filtered_and_other_stores_are_refused():
    async def go():
        Session = await session()
        async with Session() as db:
            for i, store in enumerate(("S1", "S2", "S3")):
                db.add(StoreDay(id=f"d{i}", store_id=store, business_date="2026-10-01", status="Auditing", audit_version=1))
            await db.commit()
            rows = await sd.list_store_days(None, 100, db, mgr(["S1", "S3"]))
            assert sorted(r["store_id"] for r in rows) == ["S1", "S3"]
            assert await sd.list_store_days(None, 100, db, mgr([])) == []                                  # nothing assigned, nothing seen
            assert len(await sd.list_store_days(None, 100, db, FINANCE)) == 3
            assert await sd.store_selector(db, mgr(["S3", "S1"])) == ["S1", "S3"]                          # offered exactly their stores
            with pytest.raises(HTTPException) as e:
                await sd.get_store_day("S2", "2026-10-01", None, db, mgr(["S1"]))
            assert e.value.status_code == 403
            for call in (sd.retotal_store_day("S2", "2026-10-01", db, mgr(["S1"])), sd.close_store_day("S2", "2026-10-01", db, mgr(["S1"]))):
                with pytest.raises(HTTPException) as e:
                    await call
                assert e.value.status_code == 403
    asyncio.run(go())


def test_cash_controls_and_deposits_are_scoped_for_reading_and_writing():
    async def go():
        Session = await session()
        async with Session() as db:
            for store in ("S1", "S2"):
                db.add(CashControl(id=f"c-{store}", store_id=store, business_date="2026-10-01", tender_type="Cash", declared_cash=D("10"), paid_out=D("0"), safe_drop=D("0"), declared_by="x"))
                db.add(CashDeposit(id=f"p-{store}", store_id=store, business_date="2026-10-01", deposit_reference=f"R-{store}", deposited_amount=D("10"), recorded_by="x"))
            await db.commit()
            assert [r["store_id"] for r in await cash.list_controls(None, None, db, mgr(["S2"]))] == ["S2"]
            assert [r["store_id"] for r in await cash.list_deposits(None, None, db, mgr(["S2"]))] == ["S2"]
            assert await cash.list_controls(None, None, db, mgr([])) == []
            assert await cash.list_controls("S1", None, db, mgr(["S2"])) == []        # asking for another store returns nothing
            body = cash.CashControlBody(store_id="S1", business_date="2026-10-01", declared_cash=D("5"))
            with pytest.raises(HTTPException) as e:
                await cash.declare_cash(body, db, mgr(["S2"]))
            assert e.value.status_code == 403
            dep = cash.CashDepositBody(store_id="S1", business_date="2026-10-01", deposit_reference="X", deposited_amount=D("1"))
            with pytest.raises(HTTPException) as e:
                await cash.record_deposit(dep, db, mgr(["S2"]))
            assert e.value.status_code == 403
            ok = await cash.declare_cash(cash.CashControlBody(store_id="S2", business_date="2026-10-02", declared_cash=D("5")), db, mgr(["S2"]))
            assert ok["store_id"] == "S2"
    asyncio.run(go())


# ---- the middleware, before any handler runs ---------------------------------------------------------------------
def _call(monkeypatch, user, path, method="GET"):
    from starlette.requests import Request
    from starlette.responses import PlainTextResponse

    import main

    async def fake_user(request, authorization=None):
        return user
    monkeypatch.setattr(main, "current_user", fake_user)
    request = Request({"type": "http", "method": method, "path": path, "headers": [], "query_string": b"", "scheme": "http", "server": ("t", 80), "client": ("c", 1)})
    reached = []

    async def call_next(_):
        reached.append(1)
        return PlainTextResponse("ok")
    r = asyncio.run(main.ontology_authentication(request, call_next))
    return r.status_code, bool(reached), (json.loads(r.body)["detail"] if r.status_code != 200 else None)


def test_middleware_blocks_a_store_manager_from_other_stores_paths(monkeypatch):
    m = {"user": "m", "role": "store_manager", "real_role": "store_manager", "store_scoped": True, "stores": ["S1"]}
    assert _call(monkeypatch, m, "/api/store-days/S1/2026-10-01")[:2] == (200, True)
    status, reached, detail = _call(monkeypatch, m, "/api/store-days/S2/2026-10-01/retotal", "POST")
    assert status == 403 and not reached and "S2" in detail
    assert _call(monkeypatch, m, "/api/store-days/stores")[:2] == (200, True)                         # the selector filters itself
    f = {"user": "f", "role": "finance", "real_role": "finance", "store_scoped": False, "stores": None}
    assert _call(monkeypatch, f, "/api/store-days/S2/2026-10-01")[:2] == (200, True)
