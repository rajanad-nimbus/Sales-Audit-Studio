"""Screen access: one map, enforced by the API, consistent with every endpoint's own role check."""
import pytest

import access
from auth import role_allowed


def test_every_role_can_always_reach_its_chrome_and_nothing_extra():
    for role in access.ROLES:
        assert access.api_allowed(role, "/api/me")[0] and access.api_allowed(role, "/api/batch/status")[0]
    assert [s["key"] for s in access.screens_for("viewer") if s["allowed"]] == ["settings", "settings-access"]
    assert {s["key"] for s in access.screens_for("it") if s["allowed"]} >= {"settings-retention", "settings-integrations", "settings-destinations"}
    assert "settings-retention" not in {s["key"] for s in access.screens_for("finance") if s["allowed"]}
    assert all(s["allowed"] for s in access.screens_for("admin"))
    store = {s["key"] for s in access.screens_for("store_manager") if s["allowed"]}
    assert store == {"store-days", "cash-office", "settings", "settings-access"}


def test_api_is_closed_to_roles_without_the_screen():
    for path in ("/api/cases", "/api/cases/abc/evidence", "/api/audit/page", "/api/exports/batches", "/api/transactions", "/api/store-days"):
        assert not access.api_allowed("viewer", path)[0], path
    assert not access.api_allowed("store_manager", "/api/cases")[0] and access.api_allowed("store_manager", "/api/store-days/S1/2026-10-01")[0]
    assert not access.api_allowed("it", "/api/cases/abc")[0] and access.api_allowed("it", "/api/action-center/cases")[0]   # Command Center queues
    assert not access.api_allowed("finance", "/api/ingest/stats")[0] and access.api_allowed("finance", "/api/ingest/profiles")[0]
    assert not access.api_allowed("analyst", "/api/exports/batches")[0]
    assert access.api_allowed("admin", "/api/cases")[0]
    assert "Cases" in access.api_allowed("viewer", "/api/cases")[1]


def test_the_longest_prefix_wins_and_unowned_paths_are_left_to_their_endpoint():
    assert access.api_allowed("finance", "/api/exports/destinations")[0]
    assert access.api_allowed("viewer", "/api/seed")[0] and access.api_allowed("viewer", "/api/chat")[0]   # unowned: endpoint decides


def test_screen_registry_is_well_formed():
    keys = [s.key for s in access.SCREENS]
    assert len(keys) == len(set(keys)) and len({s.path for s in access.SCREENS}) == len(access.SCREENS)
    assert all(set(s.roles) <= set(access.ROLES) - {"admin"} or s.roles == access.EVERYONE for s in access.SCREENS)


def _all_routes():
    """Every APIRoute, including those inside included routers (newer FastAPI wraps them)."""
    import main
    for route in main.app.routes:
        inner = getattr(route, "original_router", None)
        for r in (inner.routes if inner is not None else [route]):
            if hasattr(r, "dependant"):
                yield r


def _endpoint_roles():
    """(method, path) -> roles declared with require_role, read from the real FastAPI routes."""
    out = {}
    for route in _all_routes():
        for dep in route.dependant.dependencies:
            roles = getattr(dep.call, "roles", None)
            if roles:
                for m in route.methods:
                    out[(m, route.path)] = roles
    return out


def test_the_route_walk_actually_sees_the_api():
    declared = _endpoint_roles()
    assert len(declared) > 60                                    # a walk that finds nothing would pass every check below
    assert declared[("GET", "/api/exports/batches")] == ("finance", "it")
    assert ("GET", "/api/transactions") in declared and ("GET", "/api/store-days") in declared


def test_a_screen_never_shows_to_a_role_whose_own_data_calls_would_be_refused():
    """If a role may open a screen, the GET endpoints behind it that carry their own role check must let that role in.
    Otherwise the screen would open and then fail to load."""
    declared = _endpoint_roles()
    problems = []
    for s in access.SCREENS:
        for (method, path), roles in declared.items():
            if method != "GET" or not any(path == p or path.startswith(p + "/") for p in s.apis):
                continue
            owners = access._owners(path.replace("{", "x").replace("}", "x"))
            if s not in owners:
                continue   # a longer prefix belongs to another screen
            for role in access.ROLES:
                if access.can_open(role, s) and not role_allowed(role, roles, "GET", path):
                    problems.append(f"{role} may open '{s.label}' but GET {path} requires {roles}")
    assert not problems, "\n".join(problems)


def test_checked_pairs_are_not_empty():
    declared = _endpoint_roles()
    pairs = [(s.key, p) for s in access.SCREENS for (m, p) in declared
             if m == "GET" and any(p == x or p.startswith(x + "/") for x in s.all_apis) and s in access._owners(p.replace("{", "x").replace("}", "x"))]
    assert len(pairs) > 20, pairs


# ---- the enforcement point itself --------------------------------------------------------------------------------
def _call(monkeypatch, role, path, method="GET"):
    import asyncio
    import json

    from fastapi import HTTPException
    from starlette.requests import Request
    from starlette.responses import PlainTextResponse

    import main

    async def fake_user(request, authorization=None):
        if role is None:
            raise HTTPException(status_code=401, detail="Sign in")
        return {"user": "t@x", "role": role, "real_role": role}
    monkeypatch.setattr(main, "current_user", fake_user)
    request = Request({"type": "http", "method": method, "path": path, "headers": [], "query_string": b"", "scheme": "http",
                       "server": ("t", 80), "client": ("c", 1)})
    reached = []

    async def call_next(_):
        reached.append(True)
        return PlainTextResponse("ok")
    response = asyncio.run(main.ontology_authentication(request, call_next))
    return response.status_code, bool(reached), (json.loads(response.body)["detail"] if response.status_code != 200 else None)


def test_middleware_refuses_signed_in_roles_that_lack_the_screen(monkeypatch):
    status, reached, detail = _call(monkeypatch, "viewer", "/api/cases")
    assert status == 403 and not reached and "Viewer" in detail and "Cases" in detail
    assert _call(monkeypatch, "store_manager", "/api/exports/batches")[0] == 403
    assert _call(monkeypatch, "it", "/api/cases/abc/evidence")[0] == 403
    assert _call(monkeypatch, "analyst", "/api/exports/destinations")[0] == 403


def test_middleware_lets_the_right_roles_through_and_keeps_auth_rules(monkeypatch):
    for role, path in (("finance", "/api/cases"), ("admin", "/api/exports/batches"), ("store_manager", "/api/store-days"),
                       ("it", "/api/connectors"), ("viewer", "/api/me"), ("viewer", "/api/batch/status"), ("analyst", "/api/seed")):
        status, reached, _ = _call(monkeypatch, role, path)
        assert status == 200 and reached, (role, path)
    assert _call(monkeypatch, None, "/api/cases")[0] == 401              # authentication still comes first
    assert _call(monkeypatch, None, "/api/health")[0] == 200            # health stays public
