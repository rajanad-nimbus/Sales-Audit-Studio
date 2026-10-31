"""Database predicates for querying case-to-transaction audit lineage."""

from sqlalchemy import cast, func, literal_column, select
from sqlalchemy.dialects.postgresql import JSONB

from apps.audit.models import AuditEvent


def case_creation_mentions_transaction(db, transaction_id: str, audit_event=None):
    """Match a transaction ID as an array element, using the indexed PostgreSQL path."""
    if audit_event is None:
        audit_event = AuditEvent
    if db.get_bind().dialect.name == "postgresql":
        transaction_ids = cast(audit_event.after_state, JSONB).op(
            "->", return_type=JSONB,
        )(literal_column("'transaction_ids'"))
        return transaction_ids.contains([transaction_id])
    transaction_ids = func.json_each(audit_event.after_state, "$.transaction_ids").table_valued(
        "key", "value"
    )
    return select(1).select_from(transaction_ids).where(
        transaction_ids.c.value == transaction_id
    ).exists()


def case_creation_has_transactions(db, audit_event):
    """Check link presence without loading a potentially large transaction-ID array."""
    if db.get_bind().dialect.name == "postgresql":
        transaction_ids = cast(audit_event.after_state, JSONB)["transaction_ids"]
        return func.coalesce(func.jsonb_array_length(transaction_ids), 0) > 0
    return func.coalesce(func.json_array_length(audit_event.after_state, "$.transaction_ids"), 0) > 0


def case_transaction_ids_table(db, audit_event):
    """Expose a case creation event's linked IDs as rows for bounded database pagination."""
    if db.get_bind().dialect.name == "postgresql":
        transaction_ids = cast(audit_event.after_state, JSONB)["transaction_ids"]
        return func.jsonb_array_elements_text(transaction_ids).table_valued("value")
    return func.json_each(audit_event.after_state, "$.transaction_ids").table_valued("key", "value")
