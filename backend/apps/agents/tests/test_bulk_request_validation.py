"""Bulk request validation rejects unsupported actions and filter payloads."""

import pytest
from apps.agents.action_center import BulkRequest
from pydantic import ValidationError


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"action": "assign", "params": {"priority": "High"}}, "unsupported parameters for assign"),
        ({"action": "assign", "params": {"assignee": " "}}, "assignee must be a nonempty string"),
        ({"action": "priority", "params": {"priority": "Urgent"}}, "priority must be a supported priority"),
        ({"action": "snooze", "params": {"hours": True}}, "snooze hours must be greater than zero"),
        ({"action": "snooze", "params": {"hours": 9000}}, "snooze hours must be greater than zero"),
        ({"action": "unassign", "select_all": {"unknown": "value"}}, "unsupported select_all filter"),
        ({"action": "unassign", "select_all": {"store": 123}}, "select_all filter store must be a string"),
    ],
)
def test_bulk_request_rejects_invalid_action_parameters(payload, message):
    with pytest.raises(ValidationError, match=message):
        BulkRequest.model_validate(payload)


def test_bulk_request_accepts_supported_action_and_filter_parameters():
    request = BulkRequest.model_validate({
        "action": "snooze",
        "params": {"hours": 24},
        "select_all": {"stage": "open", "store": "S1,S2"},
    })

    assert request.action == "snooze"
    assert request.params == {"hours": 24}
    assert request.select_all == {"stage": "open", "store": "S1,S2"}
