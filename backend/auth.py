"""Compatibility façade for authentication and authorization.

Feature code should import these contracts from ``apps.security.authentication``.
"""

from apps.security.authentication import (
    AUTH_ENABLED,
    IMPERSONABLE,
    ONTOLOGY_AUTH_URL,
    STORE_CLAIM,
    STORE_MANAGER_CASH_PREFIXES,
    STORE_MANAGER_PREFIXES,
    current_user,
    display_name,
    require_role,
    role_allowed,
    stores_from_identity,
)

__all__ = [
    "AUTH_ENABLED",
    "IMPERSONABLE",
    "ONTOLOGY_AUTH_URL",
    "STORE_CLAIM",
    "STORE_MANAGER_CASH_PREFIXES",
    "STORE_MANAGER_PREFIXES",
    "current_user",
    "display_name",
    "require_role",
    "role_allowed",
    "stores_from_identity",
]
