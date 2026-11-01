from types import SimpleNamespace

from apps.gl.routes import _is_duplicate_cross_reference
from sqlalchemy.exc import IntegrityError


def test_mapping_conflict_matches_transaction_tender_unique_constraint():
    error = IntegrityError(
        "insert",
        {},
        Exception(
            "UNIQUE constraint failed: "
            "gl_cross_references.transaction_type, gl_cross_references.tender_type"
        ),
    )

    assert _is_duplicate_cross_reference(error)


def test_mapping_conflict_matches_default_mapping_unique_constraint():
    original = Exception("duplicate key")
    original.diag = SimpleNamespace(constraint_name="uq_gl_cross_reference_default_by_type")
    error = IntegrityError("insert", {}, original)

    assert _is_duplicate_cross_reference(error)


def test_unrelated_integrity_error_is_not_misclassified_as_duplicate_mapping():
    error = IntegrityError("insert", {}, Exception("UNIQUE constraint failed: gl_cross_references.id"))

    assert not _is_duplicate_cross_reference(error)
