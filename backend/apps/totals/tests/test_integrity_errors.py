from sqlalchemy.exc import IntegrityError

from apps.totals.routes import _is_duplicate_total_revision


def test_total_revision_conflict_matches_name_revision_index():
    error = IntegrityError(
        "insert",
        {},
        Exception(
            "UNIQUE constraint failed: "
            "audit_total_definitions.name, audit_total_definitions.revision"
        ),
    )

    assert _is_duplicate_total_revision(error)


def test_unrelated_total_integrity_error_is_not_a_revision_conflict():
    error = IntegrityError(
        "insert", {}, Exception("UNIQUE constraint failed: audit_total_definitions.id")
    )

    assert not _is_duplicate_total_revision(error)
