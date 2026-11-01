import pytest
from apps.ingest.service import parse


@pytest.mark.parametrize("declared_count", [True, False, 1.0, 1.5, -1, "1.5", "-1", " 1 ", 2**63])
def test_pos_control_count_must_be_an_exact_nonnegative_integer(declared_count):
    with pytest.raises(ValueError, match="invalid manifest values"):
        parse(
            "POSControl",
            {
                "source_record_id": "control-1",
                "business_date": "2026-10-01",
                "store_id": "S1",
                "declared_count": declared_count,
                "declared_total": "0",
            },
        )


@pytest.mark.parametrize("business_date", ["2026-02-30", "20261001", "2026-10-01T00:00:00"])
def test_pos_control_date_must_be_a_canonical_calendar_date(business_date):
    with pytest.raises(ValueError, match="invalid manifest values"):
        parse(
            "POSControl",
            {
                "source_record_id": "control-1",
                "business_date": business_date,
                "store_id": "S1",
                "declared_count": 0,
                "declared_total": "0",
            },
        )


def test_pos_control_accepts_zero_count_and_csv_integer_string():
    parsed = parse(
        "POSControl",
        {
            "source_record_id": "control-1",
            "business_date": "2026-10-01",
            "store_id": "S1",
            "declared_count": "0",
            "declared_total": "0",
        },
    )

    assert parsed["count"] == 0
    assert parsed["ts"].isoformat() == "2026-10-01T00:00:00+00:00"
