from ingest import simulate_feed


def test_simulated_feed_includes_non_pos_reconciliation_sources():
    feeds = simulate_feed("2026-10-06")
    assert {"POS", "Processor", "POSControl", "Bank", "ERP", "Shopify"} <= set(feeds)
    assert {row["record_type"] for row in feeds["Shopify"]} == {"Order", "Refund"}
    assert all(row["payment_reference"] for row in feeds["Shopify"])
