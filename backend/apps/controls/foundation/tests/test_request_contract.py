"""Request validation for normalized foundation reference identifiers."""

import pytest
from apps.controls.foundation.routes import ReferenceBody
from pydantic import ValidationError


@pytest.mark.parametrize("field", ["category", "code", "name"])
@pytest.mark.parametrize("value", ["", " ", "\t\n"])
def test_reference_request_rejects_blank_required_text(field, value):
    payload = {"category": "Store", "code": "S1", "name": "Store 1"}
    payload[field] = value

    with pytest.raises(ValidationError):
        ReferenceBody(**payload)


def test_reference_request_trims_required_text_before_persistence():
    body = ReferenceBody(category=" Store ", code=" S1 ", name=" Store 1 ")

    assert (body.category, body.code, body.name) == ("Store", "S1", "Store 1")


@pytest.mark.parametrize("traits", [
    "North",
    ["North", "  "],
    ["North", 1],
    ["North"] * 101,
])
def test_store_reference_rejects_invalid_location_traits(traits):
    with pytest.raises(ValidationError, match="location_traits"):
        ReferenceBody(
            category="Store", code="S1", name="Store 1",
            attributes={"location_traits": traits},
        )


def test_store_reference_normalizes_location_traits():
    body = ReferenceBody(
        category="Store", code="S1", name="Store 1",
        attributes={"location_traits": [" North ", "Mall"]},
    )

    assert body.attributes["location_traits"] == ["North", "Mall"]
