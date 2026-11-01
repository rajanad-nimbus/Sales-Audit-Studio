from types import SimpleNamespace

import pytest

from apps.totals.calculation import matching_transactions


def test_source_filter_matches_one_exact_ingest_lineage():
    definition = SimpleNamespace(
        source_system="POS",
        transaction_types=[],
        tender_type=None,
    )
    pos = SimpleNamespace(
        id="pos",
        source_lineage="pos-map-v1",
        transaction_type="Sale",
        tender_type="Cash",
    )
    processor = SimpleNamespace(
        id="processor",
        source_lineage="processor-map-v1",
        transaction_type="Sale",
        tender_type="Cash",
    )

    assert matching_transactions(definition, [pos, processor]) == [pos]


def test_legacy_ambiguous_source_prefix_fails_instead_of_matching_wrong_rows():
    definition = SimpleNamespace(
        source_system="P",
        transaction_types=[],
        tender_type=None,
    )

    with pytest.raises(ValueError, match="Source system must be one of"):
        matching_transactions(definition, [])
