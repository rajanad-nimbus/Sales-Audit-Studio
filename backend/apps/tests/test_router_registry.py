from fastapi import FastAPI

from apps.router_registry import ROUTERS, register_routes


def test_register_routes_attaches_all_feature_routers():
    app = FastAPI()
    register_routes(app)

    registered_paths = {route.path for route in app.routes}
    expected_paths = {route.path for router in ROUTERS for route in router.routes}
    assert expected_paths <= registered_paths
    assert any(item.cls.__name__ == "BaseHTTPMiddleware" for item in app.user_middleware)


def test_store_day_actions_are_served_at_their_public_paths():
    app = FastAPI()
    register_routes(app)
    paths = {route.path for route in app.routes}
    day = "/api/store-days/{store_id}/{business_date}"
    for action in (
        "/closure-requests",
        "/closure-requests/{request_id}/reject",
        "/close",
        "/reopen",
        "/retotal",
        "/variance-explanations",
    ):
        assert day + action in paths, action
    assert not any(path.startswith("/api/store-days/api/") for path in paths)
