from apps.controls.foundation.routes import _is_duplicate_reference_code
from sqlalchemy.exc import IntegrityError


def test_duplicate_foundation_code_matches_category_code_constraint():
    error = IntegrityError(
        "insert",
        {},
        Exception(
            "UNIQUE constraint failed: foundation_references.category, foundation_references.code"
        ),
    )

    assert _is_duplicate_reference_code(error)


def test_unrelated_foundation_integrity_error_is_not_a_duplicate_code():
    error = IntegrityError(
        "insert", {}, Exception("UNIQUE constraint failed: foundation_references.id")
    )

    assert not _is_duplicate_reference_code(error)
