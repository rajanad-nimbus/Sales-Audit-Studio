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
    for original in (error.orig, getattr(error.orig, "__cause__", None)):
        if original is None:
            continue
        names = (
            getattr(original, "constraint_name", None),
            getattr(getattr(original, "diag", None), "constraint_name", None),
        )
        if any(name in constraint_names for name in names if name is not None):
            return True
    original_message = str(error.orig)
    return any(message in original_message for message in sqlite_messages)
