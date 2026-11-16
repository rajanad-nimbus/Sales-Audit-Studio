"""Authentication and row-level authorization middleware for API requests."""

from collections.abc import Awaitable, Callable
from datetime import timedelta
import uuid

from database import async_session
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from starlette.responses import Response

from apps.audit.models import AuditEvent
from apps.controls.foundation.models import StoreLocationTrait
from apps.security import access, scope
from apps.security.authentication import current_user
from apps.security.models import StoreAssignment, VerifiedStoreUser
from model_base import utcnow


async def ontology_authentication(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Require an Ontology Studio identity and enforce API/store permissions."""
    if (
        not request.url.path.startswith("/api/")
        or request.url.path in {"/api/health", "/api/exports/_sink", "/api/ingest/shopify/webhook"}
        or request.method == "OPTIONS"
    ):
        return await call_next(request)
    try:
        request.state.user = await current_user(request, request.headers.get("authorization"))
    except HTTPException as exc:
        return JSONResponse(
            status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers or {}
        )

    user = request.state.user
    if user.get("role") in {"store_manager", "store_staff"} and not (
        user.get("real_role") == "admin" and user.get("impersonating")
    ):
        identifier = str(user.get("user") or "").strip().casefold()
        if identifier:
            assignment_identifier = (
                str(user.get("ontology_user_id") or identifier).strip().casefold()
            )
            async with async_session() as db:
                now = utcnow()
                values = {
                    "assignment_identifier": assignment_identifier,
                    "user_identifier": identifier,
                    "identity_id": str(user["ontology_user_id"])
                    if user.get("ontology_user_id")
                    else None,
                    "display_name": str(user.get("name") or identifier)[:255],
                    "role": user["role"],
                    "first_seen_at": now,
                    "last_seen_at": now,
                }
                refresh_before = now - timedelta(minutes=15)
                dialect = db.get_bind().dialect.name
                if dialect == "postgresql":
                    statement = postgres_insert(VerifiedStoreUser).values(**values)
                    statement = statement.on_conflict_do_update(
                        index_elements=[VerifiedStoreUser.assignment_identifier],
                        set_={
                            "identity_id": statement.excluded.identity_id,
                            "display_name": statement.excluded.display_name,
                            "role": statement.excluded.role,
                            "last_seen_at": statement.excluded.last_seen_at,
                        },
                        where=VerifiedStoreUser.last_seen_at < refresh_before,
                    )
                else:
                    statement = sqlite_insert(VerifiedStoreUser).values(**values)
                    statement = statement.on_conflict_do_update(
                        index_elements=[VerifiedStoreUser.assignment_identifier],
                        set_={
                            "identity_id": statement.excluded.identity_id,
                            "display_name": statement.excluded.display_name,
                            "role": statement.excluded.role,
                            "last_seen_at": statement.excluded.last_seen_at,
                        },
                        where=VerifiedStoreUser.last_seen_at < refresh_before,
                    )
                await db.execute(statement)
                await db.commit()
                assignment = (
                    await db.execute(
                        select(StoreAssignment)
                        .where(StoreAssignment.user_identifier == assignment_identifier)
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if assignment is None and assignment_identifier != identifier:
                    assignment = (
                        await db.execute(
                            select(StoreAssignment)
                            .where(StoreAssignment.user_identifier == identifier)
                            .with_for_update()
                        )
                    ).scalar_one_or_none()
                    identity_key = await db.get(StoreAssignment, assignment_identifier)
                    if assignment is not None and identity_key is None:
                        before = {
                            "user_identifier": identifier,
                            "store_ids": assignment.store_ids or [],
                            "location_traits": assignment.location_traits or [],
                        }
                        assignment.user_identifier = assignment_identifier
                        assignment.updated_by = "system.identity_migration"
                        assignment.updated_at = now
                        db.add(
                            AuditEvent(
                                id=str(uuid.uuid4()),
                                event_type="STORE_ASSIGNMENT_IDENTITY_KEYED",
                                actor="system.identity_migration",
                                object_type="StoreAssignment",
                                object_id=assignment.id,
                                action_description="Legacy email-keyed store scope migrated to verified identity ID",
                                before_state=before,
                                after_state={
                                    "user_identifier": assignment_identifier,
                                    "store_ids": assignment.store_ids or [],
                                    "location_traits": assignment.location_traits or [],
                                },
                            )
                        )
                        await db.commit()
            if assignment is not None:
                user["stores"] = assignment.store_ids or []
                user["store_location_traits"] = assignment.location_traits or []
                user["store_assignment_source"] = "local"
            else:
                user["store_assignment_source"] = "identity"
        else:
            user["stores"] = []
            user["store_assignment_source"] = "unassigned"

        traits = scope.assigned_location_traits(user)
        if traits:
            async with async_session() as db:
                user["store_scope_store_ids"] = (
                    (
                        await db.execute(
                            select(StoreLocationTrait.store_id)
                            .where(StoreLocationTrait.location_trait.in_(traits))
                            .distinct()
                        )
                    )
                    .scalars()
                    .all()
                )
        else:
            user["store_scope_store_ids"] = []

    allowed, screens = access.api_allowed(user["role"], request.url.path)
    if not allowed:
        role = user["role"]
        label = access.ROLE_LABELS.get(role, role)
        return JSONResponse(
            status_code=403,
            content={"detail": f"Your role ({label}) cannot use {' or '.join(screens)}."},
        )

    store = scope.path_store(request.url.path)
    if store is not None and not scope.store_allowed(user, store):
        return JSONResponse(
            status_code=403, content={"detail": f"You are not assigned to store {store}."}
        )
    return await call_next(request)
