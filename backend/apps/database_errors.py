"""Helpers for interpreting database constraint failures across supported dialects."""

from collections.abc import Collection

from sqlalchemy.exc import IntegrityError


def violates_unique_constraint(
    error: IntegrityError,
    *,
    constraint_names: Collection[str],
    sqlite_messages: Collection[str],
) -> bool:
    """Match a known unique constraint by server metadata or SQLite's error message."""
    constraint_name = getattr(getattr(error.orig, "diag", None), "constraint_name", None)
    if constraint_name in constraint_names:
        return True
    original_message = str(error.orig)
    return any(message in original_message for message in sqlite_messages)
