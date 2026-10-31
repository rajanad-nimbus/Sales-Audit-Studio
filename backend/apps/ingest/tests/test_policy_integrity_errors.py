from sqlalchemy.exc import IntegrityError

from apps.ingest.routes import _is_active_reconciliation_policy_conflict


def test_active_policy_race_matches_sqlite_partial_unique_index():
    error = IntegrityError(
        "insert",
        {},
        Exception("UNIQUE constraint failed: reconciliation_policies.active"),
    )

    assert _is_active_reconciliation_policy_conflict(error)


def test_unrelated_policy_integrity_error_is_not_retried_as_race():
    error = IntegrityError("insert", {}, Exception("UNIQUE constraint failed: reconciliation_policies.id"))

    assert not _is_active_reconciliation_policy_conflict(error)
