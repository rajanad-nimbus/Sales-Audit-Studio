from fastapi import FastAPI

from apps.router_registry import ROUTERS, register_routes


def test_register_routes_attaches_all_feature_routers():
    app = FastAPI()
    register_routes(app)

    registered_paths = {route.path for route in app.routes}
    expected_paths = {route.path for router in ROUTERS for route in router.routes}
    assert expected_paths <= registered_paths
    assert any(item.cls.__name__ == "BaseHTTPMiddleware" for item in app.user_middleware)
