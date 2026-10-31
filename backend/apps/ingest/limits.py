"""Request limits shared by interactive and scheduled ingestion entry points."""

MAX_DELIVERY_RECORDS = 50_000
MAX_DELIVERY_BYTES = 25 * 1024 * 1024
MAX_RECONCILIATION_TRANSACTIONS = 50_000
