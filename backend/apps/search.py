"""Shared helpers for predictable, literal text search in SQL queries."""

from sqlalchemy.sql.elements import ColumnElement


def contains_literal(column: ColumnElement[str], value: str) -> ColumnElement[bool]:
    """Match a substring while treating SQL LIKE metacharacters as ordinary text."""
    needle = value.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return column.ilike(f"%{needle}%", escape="\\")
