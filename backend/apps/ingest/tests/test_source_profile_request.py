import pytest
from apps.ingest.routes import SourceFeedProfilePatch, SourceFeedProfileRequest
from pydantic import ValidationError


def test_profile_create_normalizes_bounded_fields():
    profile = SourceFeedProfileRequest(
        name="  POS daily  ", source_system="POS", schedule=" daily ",
        column_mapping={" source_record_id ": " POS Transaction ID "},
    )

    assert profile.name == "POS daily"
    assert profile.schedule == "daily"
    assert profile.column_mapping == {"source_record_id": "POS Transaction ID"}


@pytest.mark.parametrize("values", [
    {"name": "  ", "source_system": "POS"},
    {"name": "x" * 101, "source_system": "POS"},
    {"name": "POS", "source_system": "x" * 33},
    {"name": "POS", "source_system": "POS", "schedule": "x" * 129},
    {"name": "POS", "source_system": "POS", "column_mapping": {" ": "Header"}},
    {"name": "POS", "source_system": "POS", "column_mapping": {"field": " "}},
    {"name": "POS", "source_system": "POS", "column_mapping": {"a" * 101: "Header"}},
    {"name": "POS", "source_system": "POS", "column_mapping": {"field": "H" * 101}},
    {"name": "POS", "source_system": "POS", "column_mapping": {f"field-{i}": f"H{i}" for i in range(101)}},
])
def test_profile_create_rejects_invalid_or_oversized_fields(values):
    with pytest.raises(ValidationError):
        SourceFeedProfileRequest(**values)


def test_profile_patch_rejects_explicit_null_mapping_and_normalizes_mapping():
    with pytest.raises(ValidationError, match="column_mapping must be an object"):
        SourceFeedProfilePatch(column_mapping=None)

    patch = SourceFeedProfilePatch(column_mapping={" source_record_id ": " POS ID "})
    assert patch.column_mapping == {"source_record_id": "POS ID"}
