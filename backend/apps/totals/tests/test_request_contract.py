import pytest
from pydantic import ValidationError

from apps.totals.routes import TotalDefinitionBody


def test_total_name_is_trimmed_before_persistence():
    assert TotalDefinitionBody(name="  POS Net  ").name == "POS Net"


@pytest.mark.parametrize("payload", [
    {"name": " "},
    {"name": "x" * 101},
    {"name": "Total", "category": "x" * 51},
    {"name": "Total", "source_system": "x" * 51},
    {"name": "Total", "description": "x" * 5001},
    {"name": "Total", "transaction_types": ["Sale"] * 101},
    {"name": "Total", "realms": [{}] * 101},
    {"name": "Total", "combined_operator": "++"},
    {"name": "Total", "display_group": 2**31},
    {"name": "Total", "display_group": -1},
])
def test_total_definition_rejects_fields_outside_storage_contract(payload):
    with pytest.raises(ValidationError):
        TotalDefinitionBody(**payload)


def test_total_definition_rejects_oversized_nested_metadata():
    with pytest.raises(ValidationError, match="256 KiB"):
        TotalDefinitionBody(name="Total", parameters=[{"value": "x" * (256 * 1024)}])
