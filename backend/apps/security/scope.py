"""Row-level security by store. A scoped user (a store manager) sees and changes only the stores assigned to them.

Assignments are maintained in Ontology Studio and arrive with the identity (see auth.stores_from_identity). Scope is
fail-closed: a scoped user with no assigned stores can reach none. Everyone else is unrestricted here.
"""
import re

from fastapi import HTTPException
from sqlalchemy import false

STORE_PATH = re.compile(r"^/api/store-days/([^/]+)/[^/]+")   # /api/store-days/{store}/{date}/...  (not /stores)


def scoped(user: dict) -> bool:
    return bool(user.get("store_scoped"))


def allowed_stores(user: dict) -> list[str] | None:
    """None means every store; a list (possibly empty) means only those."""
    return (user.get("stores") or []) if scoped(user) else None


def store_allowed(user: dict, store_id: str | None) -> bool:
    stores = allowed_stores(user)
    return stores is None or (store_id is not None and store_id in stores)


def require_store(user: dict, store_id: str | None) -> None:
    if not store_allowed(user, store_id):
        raise HTTPException(status_code=403, detail=f"You are not assigned to store {store_id}.")


def restrict(query, user: dict, column):
    """Limit a query to the user's stores."""
    stores = allowed_stores(user)
    if stores is None:
        return query
    return query.where(column.in_(stores)) if stores else query.where(false())


def path_store(path: str) -> str | None:
    m = STORE_PATH.match(path)
    return m.group(1) if m else None
