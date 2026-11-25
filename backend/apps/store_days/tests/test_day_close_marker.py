"""Scenario 5 (store day not ready): sales arrived but the POS Day Close marker did not."""

from apps.store_days.readiness import INCOMPLETE, with_day_close

COMPLETE = {"state": "Complete", "detail": ""}


def test_a_required_marker_that_has_not_arrived_makes_the_day_incomplete():
    view = with_day_close(COMPLETE, {"events": 12, "markers": 0}, required=True)
    assert view["state"] == INCOMPLETE
    assert view["day_close"]["state"] == "Not received"
    assert "Day Close marker has not been received" in view["detail"]


def test_the_missing_marker_is_appended_to_an_existing_detail():
    view = with_day_close({"state": "Complete", "detail": "Control total late"}, {"events": 3, "markers": 0}, required=True)
    assert view["detail"].startswith("Control total late; ")


def test_a_received_marker_leaves_the_day_complete():
    view = with_day_close(COMPLETE, {"events": 12, "markers": 1}, required=True)
    assert view["state"] == "Complete"
    assert view["day_close"]["state"] == "Received"


def test_when_the_marker_is_not_required_the_day_is_not_held_back():
    view = with_day_close(COMPLETE, {"events": 12, "markers": 0}, required=False)
    assert view["state"] == "Complete"
    assert view["day_close"]["state"] == "Not received"


def test_a_day_with_no_register_events_reports_not_provided():
    assert with_day_close(COMPLETE, None)["day_close"]["state"] == "Not provided"
