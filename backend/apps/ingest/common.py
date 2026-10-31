"""Shared ingest and reconciliation limits and source identity constants."""

from apps.ingest.limits import MAX_RECONCILIATION_TRANSACTIONS

REQUIRED = ["source_record_id", "event_time", "record_type", "amount", "store_id"]
SIGN = {"Sale": 1, "Capture": 1, "Return": -1, "Refund": -1, "Deposit": 1, "Posting": 1, "Order": 1}
VALID_TYPES = {
    "POS": {"Sale", "Return"},
    "Processor": {"Capture", "Refund"},
    "Bank": {"Deposit"},
    "ERP": {"Posting"},
    "Shopify": {"Order", "Refund"},
    "POSControl": set(),
}
LINEAGE = {
    "POS": "pos-map-v1",
    "Processor": "processor-map-v1",
    "Bank": "bank-map-v1",
    "ERP": "erp-map-v1",
    "Shopify": "shopify-map-v1",
}
RECONCILIATION_LOCK_KEY = 665302210172
SETTLEMENT_QUERY_PAGE_SIZE = 500
SHOPIFY_ORDER_KEY_BATCH_SIZE = 200
# Two bind parameters per composite key, kept below SQLite's legacy 999-bind limit.
COMPOSITE_SCOPE_BATCH_SIZE = 250
MAX_SETTLEMENT_GROUPS = MAX_RECONCILIATION_TRANSACTIONS


class ReconciliationCapacityError(RuntimeError):
    """Raised when a reconciliation scope exceeds its bounded in-memory working set."""
