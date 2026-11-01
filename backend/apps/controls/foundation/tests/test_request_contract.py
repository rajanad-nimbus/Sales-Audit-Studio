"""Request validation for normalized foundation reference identifiers."""

import pytest
from pydantic import ValidationError

from apps.controls.foundation.routes import ReferenceBody


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
