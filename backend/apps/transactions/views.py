"""Stable response projections shared by transaction endpoints."""

from apps.ingest.models import SourceRecord
from apps.transactions.models import CanonicalTransaction


def row_view(
    tx: CanonicalTransaction, source: SourceRecord | None, items: int | None = None
) -> dict:
    return {
        "item_count": items,
        "detail_status": tx.detail_status,
        "original_reference": tx.original_reference,
        "id": tx.id,
        "business_date": tx.business_date,
        "event_timestamp": tx.event_timestamp,
        "store_id": tx.store_id,
        "register_id": tx.register_id,
        "source_system": source.source_system if source else "Unknown",
        "source_record_id": source.source_record_id if source else None,
        "transaction_type": tx.transaction_type,
        "tender_type": tx.tender_type,
        "payment_reference": tx.payment_reference,
        "amount": tx.signed_amount,
        "currency": tx.currency,
        "reconciliation_status": tx.reconciliation_status,
    }
