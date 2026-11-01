"""Identity and access information endpoints."""

import httpx
from database import get_db
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, union
from sqlalchemy.ext.asyncio import AsyncSession

from apps.cash.models import CashControl
from apps.controls.foundation.models import FoundationReference
from apps.security import access, scope
from apps.security import authentication as auth
from apps.security.authentication import current_user
from apps.store_days.models import StoreDay
from apps.transactions.models import CanonicalTransaction

router = APIRouter()


@router.get("/api/me")
async def me(user: dict = Depends(current_user)):
    """Return identity and the screens available to the signed-in user."""
    return {
        **user,
        "role_label": access.ROLE_LABELS.get(user["role"], user["role"]),
        "screens": access.screens_for(user["role"]),
    }


@router.post("/api/auth/logout")
async def logout(request: Request, user: dict = Depends(current_user)):
    """End the upstream session when possible and always clear the local cookie."""
    ended = False
    cookie = request.cookies.get("app_session_id")
    base = auth.ONTOLOGY_AUTH_URL.rsplit("/auth/", 1)[0] if auth.ONTOLOGY_AUTH_URL else ""
    if base and cookie:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                ended = (
                    await client.post(
                        f"{base}/auth/logout", headers={"Cookie": f"app_session_id={cookie}"}
                    )
                ).status_code < 400
        except httpx.HTTPError:
            pass
    response = JSONResponse({"success": True, "ontology_session_ended": ended})
    response.delete_cookie("app_session_id", path="/")
    return response


@router.get("/api/access/matrix")
async def access_matrix(user: dict = Depends(current_user)):
    return {**access.matrix(), "you": user["role"]}


@router.get("/api/stores")
async def store_choices(db: AsyncSession = Depends(get_db), user: dict = Depends(current_user)):
    """Stores to offer in any store field. A store manager gets only their own; everyone else gets every known store."""
    if scope.scoped(user):
        return sorted(scope.allowed_stores(user) or [])
    store_codes = union(
        select(FoundationReference.code.label("store_id")).where(
            FoundationReference.category == "Store",
            FoundationReference.active.is_(True),
            FoundationReference.code.is_not(None),
        ),
        select(CanonicalTransaction.store_id.label("store_id")).where(
            CanonicalTransaction.store_id.is_not(None),
        ),
        select(StoreDay.store_id.label("store_id")).where(StoreDay.store_id.is_not(None)),
    ).subquery()
    codes = (
        (await db.execute(select(store_codes.c.store_id).order_by(store_codes.c.store_id)))
        .scalars()
        .all()
    )
    return [code for code in codes if code]


async def _lookup(
    db: AsyncSession, user: dict, category: str, txn_column, control_column, store_id: str | None
) -> list[dict]:
    """Registers or cashiers to offer in a search box: the governed list plus whatever has actually been seen."""
    options: dict[str, str | None] = {}
    if not scope.scoped(user):  # a store manager only sees what happened in their own stores
        for ref in (
            await db.execute(
                select(FoundationReference).where(
                    FoundationReference.category == category, FoundationReference.active.is_(True)
                )
            )
        ).scalars():
            options[ref.code] = ref.name
    for model, column in ((CanonicalTransaction, txn_column), (CashControl, control_column)):
        query = scope.restrict(
            select(column).where(column.is_not(None)).distinct(), user, model.store_id
        )
        if store_id:
            query = query.where(model.store_id == store_id)
        for code in (await db.execute(query)).scalars():
            options.setdefault(code, None)
    return [
        {"code": code, "name": name if name != code else None}
        for code, name in sorted(options.items())
    ]


@router.get("/api/lookups/registers")
async def register_choices(
    store_id: str | None = Query(default=None, max_length=50),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(current_user),
):
    return await _lookup(
        db, user, "Register", CanonicalTransaction.register_id, CashControl.register_id, store_id
    )


@router.get("/api/lookups/cashiers")
async def cashier_choices(
    store_id: str | None = Query(default=None, max_length=50),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(current_user),
):
    return await _lookup(
        db, user, "Cashier", CanonicalTransaction.cashier_id, CashControl.cashier_id, store_id
    )
