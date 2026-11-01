import pytest
from pydantic import ValidationError

from apps.exports.schemas import DestIn, FailedExportResolution, RunIn


def test_destination_schema_normalizes_name_and_bounds_database_fields():
    destination = DestIn(name="  ERP  ", kind="file")

    assert destination.name == "ERP"

    with pytest.raises(ValidationError):
        DestIn(name="   ", kind="file")
    with pytest.raises(ValidationError):
        DestIn(name="x" * 101, kind="file")
    with pytest.raises(ValidationError):
        DestIn(name="ERP", kind="webhook", url="x" * 2049)
    with pytest.raises(ValidationError):
        DestIn(name="ERP", kind="webhook", url="http://")
    with pytest.raises(ValidationError):
        DestIn(name="ERP", kind="webhook", url="ftp://erp.example")
    with pytest.raises(ValidationError):
        DestIn(name="ERP", kind="webhook", secret="x" * 4097)
    with pytest.raises(ValidationError, match="at least 32 characters"):
        DestIn(name="ERP", kind="webhook", url="https://erp.example")
    with pytest.raises(ValidationError, match="signing secret"):
        DestIn(name="ERP", kind="webhook", url="https://erp.example", secret="  ")
    with pytest.raises(ValidationError, match="at least 32 characters"):
        DestIn(name="ERP", kind="webhook", url="https://erp.example", secret="short-secret")

    webhook_secret = "s" * 32
    webhook = DestIn(name="ERP", kind="webhook", url="https://erp.example", secret=webhook_secret)
    assert webhook.secret == webhook_secret
    assert str(webhook.url) == "https://erp.example/"


def test_failed_export_resolution_rejects_unknown_outcomes():
    with pytest.raises(ValidationError):
        FailedExportResolution(outcome="not-recieved", evidence="operator checked receiver")

    assert (
        FailedExportResolution(
            outcome="not_received",
            evidence="operator checked receiver",
        ).outcome
        == "not_received"
    )


def test_export_request_limits_lookup_identifiers():
    with pytest.raises(ValidationError):
        RunIn(destination_id="d" * 37, dataset="audit")
