"""Stable response projections shared by transaction endpoints."""

import json
import re

from apps.ingest.models import SourceRecord
from apps.security.sensitive_data import redact_sensitive_fields
from apps.transactions.models import CanonicalTransaction


def mask_customer_id(value: str | None) -> str | None:
    """Return a stable, non-identifying display value for a customer reference."""
    if value is None:
        return None
    return f"••••{value[-4:]}" if len(value) > 4 else "••••"


def redact_customer_id_preview(text: str) -> str:
    """Mask a possibly truncated JSON string without requiring it to parse."""
    pattern = re.compile(r'("customer_id"\s*:\s*)("(?:\\.|[^"\\])*"|[^,}\]\s]+)')
    return pattern.sub(
        lambda match: f"{match.group(1)}null"
        if match.group(2) == "null"
        else f'{match.group(1)}"••••"',
        text,
    )


def redact_customer_ids(value):
    """Mask customer identifiers recursively in a source-payload preview."""
    if isinstance(value, dict):
        return {
            key: mask_customer_id(str(item))
            if key == "customer_id" and item is not None
            else redact_customer_ids(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_customer_ids(item) for item in value]
    return value


def row_view(
    tx: CanonicalTransaction, source: SourceRecord | None, items: int | None = None
) -> dict:
    return redact_sensitive_fields(
        {
            "item_count": items,
            "detail_status": tx.detail_status,
            "original_reference": tx.original_reference,
            "id": tx.id,
            "business_date": tx.business_date,
            "event_timestamp": tx.event_timestamp,
            "store_id": tx.store_id,
            "customer_id": mask_customer_id(tx.customer_id),
            "customer_identification_type": tx.customer_identification_type,
            "user_references": tx.user_references,
            "paid_out_details": paid_out_details(tx, source),
            "register_id": tx.register_id,
            "cashier_id": tx.cashier_id,
            "salesperson_id": tx.salesperson_id,
            "source_system": source.source_system if source else "Unknown",
            "source_record_id": source.source_record_id if source else None,
            "transaction_type": tx.transaction_type,
            "tender_type": tx.tender_type,
            "payment_reference": tx.payment_reference,
            "amount": tx.signed_amount,
            "currency": tx.currency,
            "reconciliation_status": tx.reconciliation_status,
        }
    )


def paid_out_details(tx: CanonicalTransaction, source: SourceRecord | None) -> dict:
    """Return approved canonical details, falling back to the immutable source for older records."""
    if tx.paid_out_details is not None:
        return redact_sensitive_fields(tx.paid_out_details)
    if source is None or not source.payload:
        return {}
    try:
        payload = json.loads(source.payload)
    except (TypeError, ValueError):
        return {}
    details = payload.get("details") if isinstance(payload, dict) else None
    if not isinstance(details, dict):
        return {}
    allowed = {
        "vendor_number",
        "vendor_invoice_number",
        "proof_of_delivery_number",
        "payment_reference_number",
    }
    return redact_sensitive_fields({key: value for key, value in details.items() if key in allowed})
