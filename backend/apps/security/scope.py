"""Row-level security by store for assigned auditors and store employees.

Assignments are maintained in ZeTSA or arrive with the identity provider. Scope is fail-closed: a scoped user with no
assigned stores can reach none. Everyone else is unrestricted here.
"""

import re

from fastapi import HTTPException
from sqlalchemy import false, or_, select

from apps.controls.foundation.models import StoreLocationTrait

STORE_PATH = re.compile(
    r"^/api/store-days/([^/]+)/[^/]+"
)  # /api/store-days/{store}/{date}/...  (not /stores)


def scoped(user: dict) -> bool:
    return bool(user.get("store_scoped"))


def allowed_stores(user: dict) -> list[str] | None:
    """None means every store; a list (possibly empty) means only those."""
    return (user.get("stores") or []) if scoped(user) else None


def assigned_location_traits(user: dict) -> list[str]:
    return user.get("store_location_traits") or []


def store_predicate(user: dict, column):
    """SQL predicate for explicit stores plus live Foundation location-trait membership."""
    stores = allowed_stores(user)
    if stores is None:
        return None
    conditions = [column.in_(stores)] if stores else []
    traits = assigned_location_traits(user)
    if traits:
        conditions.append(
            column.in_(
                select(StoreLocationTrait.store_id).where(
                    StoreLocationTrait.location_trait.in_(traits)
                )
            )
        )
    return or_(*conditions) if conditions else false()


def store_allowed(user: dict, store_id: str | None) -> bool:
    stores = allowed_stores(user)
    return stores is None or (
        store_id is not None
        and (
            store_id in stores
            or store_id in (user.get("store_scope_store_ids") or [])
        )
    )


def require_store(user: dict, store_id: str | None) -> None:
    if not store_allowed(user, store_id):
        raise HTTPException(status_code=403, detail=f"You are not assigned to store {store_id}.")


def restrict(query, user: dict, column):
    """Limit a query to the user's stores."""
    predicate = store_predicate(user, column)
    if predicate is None:
        return query
    return query.where(predicate)


def path_store(path: str) -> str | None:
    m = STORE_PATH.match(path)
    return m.group(1) if m else None
