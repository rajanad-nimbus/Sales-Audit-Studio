"""CSV ingestion should enforce limits as rows are parsed."""
import pytest

from apps.ingest import contracts


def test_csv_parser_applies_profile_mapping_during_parse():
    rows = contracts.parse_csv_delivery(
        "source_id,amount\nA,10.00\nB,20.00\n",
        {"source_record_id": "source_id", "amount": "amount"},
    )

    assert rows == [
        {"source_record_id": "A", "amount": "10.00"},
        {"source_record_id": "B", "amount": "20.00"},
    ]


def test_csv_parser_stops_at_first_row_over_record_limit(monkeypatch):
    monkeypatch.setattr(contracts, "MAX_DELIVERY_RECORDS", 2)

    with pytest.raises(ValueError, match="exceeds the 2-record delivery limit"):
        contracts.parse_csv_delivery("id\n1\n2\n3\n4\n")


def test_csv_parser_rejects_header_without_data_rows():
    with pytest.raises(ValueError, match="contains no data rows"):
        contracts.parse_csv_delivery("id,amount\n")
