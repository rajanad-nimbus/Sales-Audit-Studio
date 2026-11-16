from apps.system.routes import router


def test_system_router_exposes_platform_endpoints():
    paths = {route.path for route in router.routes}

    assert paths == {
        "/",
        "/api/health",
        "/api/production-readiness",
        "/api/connectors",
        "/api/connectors/{connector_id}/{action}",
    } | {path for path in paths if path.startswith("/api/system-options/")}
    assert {
        "/api/system-options/package",
        "/api/system-options/package/preview",
        "/api/system-options/package/apply",
    } <= paths
