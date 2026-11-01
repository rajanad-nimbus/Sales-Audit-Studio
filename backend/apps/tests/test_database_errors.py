from types import SimpleNamespace

from sqlalchemy.exc import IntegrityError

from apps.database_errors import violates_unique_constraint


def test_unique_violation_matches_database_constraint_metadata():
    original = Exception("duplicate key")
    original.diag = SimpleNamespace(constraint_name="uq_expected")
    error = IntegrityError("insert", {}, original)

    assert violates_unique_constraint(
        error,
        constraint_names={"uq_expected"},
        sqlite_messages=set(),
    )


def test_unique_violation_matches_sqlite_message():
    error = IntegrityError("insert", {}, Exception("UNIQUE constraint failed: example.code"))

    assert violates_unique_constraint(
        error,
        constraint_names={"uq_expected"},
        sqlite_messages={"UNIQUE constraint failed: example.code"},
    )


def test_unique_violation_matches_underlying_asyncpg_constraint():
    driver_error = Exception("duplicate key")
    driver_error.constraint_name = "uq_expected"
    original = Exception("translated error")
    original.__cause__ = driver_error
    error = IntegrityError("insert", {}, original)

    assert violates_unique_constraint(
        error,
        constraint_names={"uq_expected"},
        sqlite_messages=set(),
    )
    assert not violates_unique_constraint(
        error,
        constraint_names={"uq_other"},
        sqlite_messages=set(),
    )


def test_unrecognized_constraint_is_not_misclassified():
    error = IntegrityError("insert", {}, Exception("UNIQUE constraint failed: example.id"))

    assert not violates_unique_constraint(
        error,
        constraint_names={"uq_expected"},
        sqlite_messages={"UNIQUE constraint failed: example.code"},
    )
