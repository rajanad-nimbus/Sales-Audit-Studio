from types import SimpleNamespace

from apps.operations.batch import _is_active_batch_conflict
from sqlalchemy.exc import IntegrityError


def test_active_batch_conflict_matches_the_business_date_constraint():
    error = IntegrityError(
        "insert",
        {},
        Exception("UNIQUE constraint failed: batch_runs.business_date"),
    )

    assert _is_active_batch_conflict(error)


def test_active_batch_conflict_matches_postgres_constraint_metadata():
    original = Exception("duplicate key")
    original.diag = SimpleNamespace(constraint_name="uq_batch_runs_one_running_per_date")
    error = IntegrityError("insert", {}, original)

    assert _is_active_batch_conflict(error)


def test_unrelated_integrity_error_is_not_misclassified_as_active_batch():
    error = IntegrityError("insert", {}, Exception("UNIQUE constraint failed: batch_runs.id"))

    assert not _is_active_batch_conflict(error)
