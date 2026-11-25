"""Ontology Studio authenticates; Nimbus decides the business role, and never for a Studio administrator."""

import asyncio

import pytest
from fastapi import HTTPException
from models import AuditEvent
from pydantic import ValidationError
from sqlalchemy import select
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from apps.security import middleware
from apps.security.models import StoreAssignment, UserRoleAssignment, VerifiedStoreUser
from apps.security.routes import (
    RoleAssignmentInput,
    list_role_assignments,
    remove_role_assignment,
    set_role_assignment,
)

ADMIN = {"user": "admin@example.test", "role": "admin"}
REASON = "Covers the finance desk from Monday"


def studio_user(role="viewer", real_role="viewer", identity_id="u-7", **extra):
    return {
        "user": "pat@example.test",
        "name": "Pat Doe",
        "role": role,
        "real_role": real_role,
        "impersonating": role != real_role,
        "ontology_user_id": identity_id,
        "store_scoped": role == "analyst",
        "stores": [],
        **extra,
    }


def through_middleware(monkeypatch, user, path="/api/me"):
    """Send one request through the real middleware and return (status, the user it authorized)."""

    async def fake_user(request, authorization=None):
        return dict(user)

    monkeypatch.setattr(middleware, "current_user", fake_user)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "server": ("t", 80),
            "client": ("c", 1),
        }
    )
    seen = {}

    async def call_next(inner):
        seen["user"] = inner.state.user
        return PlainTextResponse("ok")

    response = asyncio.run(middleware.ontology_authentication(request, call_next))
    return response.status_code, seen.get("user")


def listing_of(db, *, q=None, assigned="all", page=1, page_size=50):
    """Call the list route directly, spelling out every filter whose default is a FastAPI Query object."""
    return list_role_assignments(
        q=q,
        assigned=assigned,
        assigned_role=None,
        inactive_days=None,
        page=page,
        page_size=page_size,
        format="json",
        db=db,
        _=ADMIN,
    )


def db_run(work):
    async def go():
        async with middleware.async_session() as db:
            return await work(db)

    return asyncio.run(go())


def test_a_viewer_in_studio_becomes_finance_when_nimbus_assigns_it(monkeypatch):
    status, user = through_middleware(monkeypatch, studio_user())
    assert status == 200 and user["role"] == "viewer" and user["role_source"] == "identity"

    async def verify_then_assign(db):
        # They signed in above, so they are in the directory and can be assigned.
        await set_role_assignment(
            RoleAssignmentInput(assignment_identifier="u-7", role="finance", reason=REASON),
            db,
            ADMIN,
        )

    db_run(verify_then_assign)
    status, user = through_middleware(monkeypatch, studio_user())
    assert status == 200
    assert (user["role"], user["role_source"], user["store_scoped"]) == (
        "finance",
        "assignment",
        False,
    )
    assert user["real_role"] == "viewer"  # Studio's own answer is kept alongside


def test_an_assigned_sales_auditor_is_scoped_to_their_local_portfolio(monkeypatch):
    through_middleware(monkeypatch, studio_user())

    async def assign(db):
        await set_role_assignment(
            RoleAssignmentInput(assignment_identifier="u-7", role="analyst", reason=REASON),
            db,
            ADMIN,
        )
        db.add(
            StoreAssignment(
                id="s1", user_identifier="u-7", store_ids=["S1"], updated_by="admin@example.test"
            )
        )
        await db.commit()

    db_run(assign)
    status, user = through_middleware(monkeypatch, studio_user(), "/api/store-days")
    assert status == 200
    assert user["role"] == "analyst" and user["store_scoped"] and user["stores"] == ["S1"]
    assert user["store_assignment_source"] == "local"
    # ...and another store's day is refused by the same middleware.
    blocked, _ = through_middleware(monkeypatch, studio_user(), "/api/store-days/S2/2026-10-01")
    assert blocked == 403


def test_a_sales_auditor_without_a_local_portfolio_sees_no_stores(monkeypatch):
    """ReSA ties audit users to locations; with no assignment the auditor fails closed."""
    through_middleware(monkeypatch, studio_user())

    async def assign(db):
        await set_role_assignment(
            RoleAssignmentInput(assignment_identifier="u-7", role="analyst", reason=REASON),
            db,
            ADMIN,
        )

    db_run(assign)
    status, user = through_middleware(monkeypatch, studio_user(), "/api/store-days")
    assert status == 200 and user["stores"] == [] and user["store_scoped"]
    assert user["store_assignment_source"] == "unassigned"


def test_a_studio_administrator_is_never_given_an_assigned_role_or_recorded(monkeypatch):
    async def stray_assignment(db):
        db.add(
            UserRoleAssignment(
                assignment_identifier="u-1", role="auditor", reason=REASON, assigned_by="x"
            )
        )
        await db.commit()

    db_run(stray_assignment)
    admin = studio_user(role="admin", real_role="admin", identity_id="u-1")
    status, user = through_middleware(monkeypatch, admin)
    assert status == 200 and user["role"] == "admin" and user["role_source"] == "identity"
    assert db_run(lambda db: db.get(VerifiedStoreUser, "u-1")) is None


def test_removing_the_assignment_gives_the_studio_role_back(monkeypatch):
    through_middleware(monkeypatch, studio_user())

    async def assign_then_remove(db):
        await set_role_assignment(
            RoleAssignmentInput(assignment_identifier="u-7", role="it", reason=REASON), db, ADMIN
        )

    db_run(assign_then_remove)
    assert through_middleware(monkeypatch, studio_user())[1]["role"] == "it"
    db_run(lambda db: remove_role_assignment("u-7", "Moved to another team", db, ADMIN))
    after = through_middleware(monkeypatch, studio_user())[1]
    assert (after["role"], after["role_source"]) == ("viewer", "identity")


def test_the_admin_api_validates_audits_and_lists(monkeypatch):
    with pytest.raises(ValidationError):
        RoleAssignmentInput(
            assignment_identifier="u-7", role="admin", reason=REASON
        )  # not assignable
    with pytest.raises(ValidationError):
        RoleAssignmentInput(assignment_identifier="u-7", role="finance", reason="short")

    through_middleware(monkeypatch, studio_user())  # they sign in, so they are in the directory

    async def scenario(db):
        with pytest.raises(HTTPException) as unknown:
            await set_role_assignment(
                RoleAssignmentInput(assignment_identifier="ghost", role="it", reason=REASON),
                db,
                ADMIN,
            )
        assert unknown.value.status_code == 422 and "sign in" in unknown.value.detail

        view = await set_role_assignment(
            RoleAssignmentInput(assignment_identifier="U-7 ", role="auditor", reason=REASON),
            db,
            ADMIN,
        )
        assert view["assigned_role"] == "auditor" and view["assigned_by"] == ADMIN["user"]
        changed = await set_role_assignment(
            RoleAssignmentInput(assignment_identifier="u-7", role="analyst", reason=REASON + "!"),
            db,
            ADMIN,
        )
        assert changed["assigned_role"] == "analyst"

        listing = await listing_of(db)
        assert [role["key"] for role in listing["roles"]][:2] == ["finance", "it"]
        assert [i["assigned_role"] for i in listing["items"]] == ["analyst"]
        assert (await listing_of(db, assigned="unassigned"))["items"] == []
        assert (await listing_of(db, q="pat", assigned="assigned"))["pagination"][
            "total"
        ] == 1

        with pytest.raises(HTTPException) as nothing:
            await remove_role_assignment("nobody", "Housekeeping removal", db, ADMIN)
        assert nothing.value.status_code == 404
        await remove_role_assignment("u-7", "Housekeeping removal", db, ADMIN)
        events = (
            (await db.execute(select(AuditEvent).order_by(AuditEvent.created_at))).scalars().all()
        )
        assert [e.event_type for e in events] == [
            "ROLE_ASSIGNMENT_SET",
            "ROLE_ASSIGNMENT_SET",
            "ROLE_ASSIGNMENT_REMOVED",
        ]
        assert events[1].before_state == {"role": "auditor"}

    db_run(scenario)
