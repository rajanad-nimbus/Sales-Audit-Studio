from apps.controls.rules.routes import _is_duplicate_rule_name
from sqlalchemy.exc import IntegrityError


def test_rule_name_conflict_matches_sqlite_unique_constraint():
    error = IntegrityError(
        "insert",
        {},
        Exception("UNIQUE constraint failed: configured_audit_rules.name"),
    )

    assert _is_duplicate_rule_name(error)


def test_unrelated_rule_integrity_error_is_not_a_name_conflict():
    error = IntegrityError("insert", {}, Exception("UNIQUE constraint failed: configured_audit_rules.id"))

    assert not _is_duplicate_rule_name(error)
