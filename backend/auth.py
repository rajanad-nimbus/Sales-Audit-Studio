"""Nimbus authorization delegated to Ontology Studio."""
import os

import httpx
from fastapi import Depends, Header, HTTPException, Request


ONTOLOGY_AUTH_URL = os.getenv("NIMBUS_ONTOLOGY_AUTH_URL", "").rstrip("/")
AUTH_ENABLED = bool(ONTOLOGY_AUTH_URL)
IMPERSONABLE = {"finance", "it", "analyst", "auditor", "store_manager"}
# store_manager is limited to these areas, and within them to the stores assigned to the person in Ontology Studio.
STORE_MANAGER_PREFIXES = ("/api/cash", "/api/store-days")
# Where the identity carries the user's stores: a dotted path into the Ontology Studio user object (a list, or a comma-separated string).
STORE_CLAIM = os.getenv("NIMBUS_ONTOLOGY_STORE_CLAIM", "stores")


def stores_from_identity(identity: dict, claim: str | None = None) -> list[str]:
    """The stores Ontology Studio assigns to this user. Missing, malformed or empty means none, which is fail-closed."""
    value = identity
    for part in (claim or STORE_CLAIM).split("."):
        value = value.get(part) if isinstance(value, dict) else None
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for v in value:
        code = str(v).strip()
        if code and code not in out:
            out.append(code)
    return out


def _role_map() -> dict[str, str]:
    raw = os.getenv("NIMBUS_ONTOLOGY_ROLE_MAP", "admin:admin,owner:admin,finance:finance,it:it,analyst:analyst,auditor:auditor,store_manager:store_manager")
    return dict(item.split(":", 1) for item in raw.split(",") if ":" in item)


async def current_user(request: Request, authorization: str | None = Header(default=None)) -> dict:
    """Ask Ontology Studio to validate the submitted user JWT."""
    if not ONTOLOGY_AUTH_URL:
        raise HTTPException(status_code=503, detail="Ontology authentication is not configured")
    headers = {}
    if authorization and authorization.lower().startswith("bearer "):
        headers["Authorization"] = authorization
    elif session := request.cookies.get("app_session_id"):
        # Ontology Studio's HTTP-only cookie is shared by localhost services.
        # Forward it only to the configured Ontology Studio identity endpoint.
        headers["Cookie"] = f"app_session_id={session}"
    else:
        raise HTTPException(status_code=401, detail="Sign in through Ontology Studio or provide its bearer token",
                            headers={"WWW-Authenticate": "Bearer"})
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(ONTOLOGY_AUTH_URL, headers=headers)
    except httpx.HTTPError:
        raise HTTPException(status_code=503, detail="Ontology authentication service is unavailable")
    if response.status_code in (401, 403):
        raise HTTPException(status_code=401, detail="Invalid or expired Ontology Studio token",
                            headers={"WWW-Authenticate": "Bearer"})
    if response.status_code != 200:
        raise HTTPException(status_code=503, detail="Ontology authentication service could not verify the token")
    try:
        identity = response.json()["user"]
        ontology_role = str(identity.get("role") or "").lower()
        real_role = _role_map().get(ontology_role, "viewer")
        role = real_role
        # Only a real admin may act as another role; the header is ignored for everyone else.
        asked = (request.headers.get("x-impersonate-role") or "").lower()
        if real_role == "admin" and asked in IMPERSONABLE:
            role = asked
        # Row-level scope. A store manager sees only assigned stores. An admin acting as one chooses the stores to simulate,
        # because Ontology holds no assignment for the admin. None means unrestricted.
        stores = None
        if role == "store_manager":
            if real_role == "admin":
                stores = [s.strip() for s in (request.headers.get("x-impersonate-stores") or "").split(",") if s.strip()]
            else:
                stores = stores_from_identity(identity)
        return {"user": identity.get("email") or identity.get("username") or identity.get("open_id"),
                "role": role, "real_role": real_role, "impersonating": role != real_role,
                "ontology_role": ontology_role, "ontology_user_id": identity.get("id"),
                "store_scoped": role == "store_manager", "stores": stores}
    except (ValueError, KeyError, TypeError):
        raise HTTPException(status_code=503, detail="Ontology authentication response was invalid")


def role_allowed(role: str, roles: tuple[str, ...], method: str, path: str) -> bool:
    """Derive the extra roles from how each endpoint is already declared.

    finance-only endpoints are approvals and stay finance-only; endpoints open to both finance and it are shared
    work, which analysts may do; auditors may read whatever finance or it may read; store managers get cash and
    store-day access only.
    """
    if role == "admin" or role in roles:
        return True
    shared = "finance" in roles and "it" in roles
    if role == "analyst":
        return shared
    if role == "auditor":
        return method == "GET" and ("finance" in roles or "it" in roles)
    if role == "store_manager":
        if not shared or not path.startswith(STORE_MANAGER_PREFIXES):
            return False
        return method == "GET" or path.startswith("/api/cash")
    return False


def require_role(*roles: str):
    async def dep(request: Request, user: dict = Depends(current_user)) -> dict:
        if not role_allowed(user["role"], roles, request.method, request.url.path):
            raise HTTPException(status_code=403, detail=f"Requires role: {', '.join(roles)}")
        return user
    dep.roles = roles   # read by tests that compare each endpoint with the screen access map
    return dep
