"""Authentication and row-level authorization middleware for API requests."""

from collections.abc import Awaitable, Callable

import access
import scope
from auth import current_user
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response


async def ontology_authentication(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Require an Ontology Studio identity and enforce API/store permissions."""
    if (
        not request.url.path.startswith("/api/")
        or request.url.path in {"/api/health", "/api/exports/_sink"}
        or request.method == "OPTIONS"
    ):
        return await call_next(request)
    try:
        request.state.user = await current_user(request, request.headers.get("authorization"))
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers or {})

    allowed, screens = access.api_allowed(request.state.user["role"], request.url.path)
    if not allowed:
        role = request.state.user["role"]
        label = access.ROLE_LABELS.get(role, role)
        return JSONResponse(
            status_code=403,
            content={"detail": f"Your role ({label}) cannot use {' or '.join(screens)}."},
        )

    store = scope.path_store(request.url.path)
    if store is not None and not scope.store_allowed(request.state.user, store):
        return JSONResponse(status_code=403, content={"detail": f"You are not assigned to store {store}."})
    return await call_next(request)
