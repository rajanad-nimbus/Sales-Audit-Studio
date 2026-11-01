import pytest
from apps.ingest import contracts
from pydantic import ValidationError

CsvIngestRequest = contracts.CsvIngestRequest
IngestRequest = contracts.IngestRequest


@pytest.mark.parametrize("model, values", [
    (IngestRequest, {"records": []}),
    (CsvIngestRequest, {"csv_data": "source_record_id\n1"}),
])
def test_ingest_requests_reject_blank_or_oversized_identifiers(model, values):
    for field, invalid in (
        ("source_system", " "),
        ("source_system", "x" * 51),
        ("delivery_id", ""),
        ("delivery_id", "x" * 256),
    ):
        with pytest.raises(ValidationError):
            request = {**values, "source_system": "POS", "delivery_id": "delivery"}
            request[field] = invalid
            model(**request)


def test_ingest_requests_accept_database_sized_identifiers():
    assert IngestRequest(source_system="POS", delivery_id="delivery", records=[])
    assert CsvIngestRequest(source_system="POS", delivery_id="delivery", csv_data="record\n1")


def test_json_payload_size_counts_utf8_without_exceeding_limit():
    assert contracts.json_payload_size([{"name": "café"}], 100) == len('[{"name":"café"}]'.encode("utf-8"))
    assert contracts.json_payload_size([{"name": "café"}], 5) > 5


def test_ingest_request_rejects_payload_over_delivery_limit(monkeypatch):
    monkeypatch.setattr(contracts, "MAX_DELIVERY_BYTES", 8)
    with pytest.raises(ValidationError, match="Delivery payload exceeds"):
        IngestRequest(source_system="POS", delivery_id="delivery", records=[{"value": "long"}])
