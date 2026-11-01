from apps.ingest.routes import _is_duplicate_profile_name
from sqlalchemy.exc import IntegrityError


def test_profile_name_conflict_matches_unique_name_constraint():
    error = IntegrityError(
        "insert",
        {},
        Exception("UNIQUE constraint failed: source_feed_profiles.name"),
    )

    assert _is_duplicate_profile_name(error)


def test_unrelated_profile_integrity_error_is_not_a_name_conflict():
    error = IntegrityError("insert", {}, Exception("UNIQUE constraint failed: source_feed_profiles.id"))

    assert not _is_duplicate_profile_name(error)
