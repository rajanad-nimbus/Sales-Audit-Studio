"""Database predicates for querying case-to-transaction audit lineage."""

from sqlalchemy import String, cast, func, literal_column, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import aliased

from apps.audit.models import AuditEvent


def case_creation_mentions_transaction(db, transaction_id: str, audit_event=None):
    """Match a transaction ID as an array element, using the indexed PostgreSQL path."""
    if audit_event is None:
        audit_event = AuditEvent
    if db.get_bind().dialect.name == "postgresql":
        transaction_ids = cast(audit_event.after_state, JSONB).op(
            "->",
            return_type=JSONB,
        )(literal_column("'transaction_ids'"))
        return transaction_ids.contains([transaction_id])
    transaction_ids = func.json_each(audit_event.after_state, "$.transaction_ids").table_valued(
        "key", "value"
    )
    return (
        select(1)
        .select_from(transaction_ids)
        .where(transaction_ids.c.value == transaction_id)
        .exists()
    )


def case_creation_has_transactions(db, audit_event):
    """Check link presence without loading a potentially large transaction-ID array."""
    if db.get_bind().dialect.name == "postgresql":
        transaction_ids = cast(audit_event.after_state, JSONB)["transaction_ids"]
        return func.coalesce(func.jsonb_array_length(transaction_ids), 0) > 0
    return (
        func.coalesce(func.json_array_length(audit_event.after_state, "$.transaction_ids"), 0) > 0
    )


def case_transaction_ids_table(db, audit_event):
    """Expose a case creation event's linked IDs as rows for bounded database pagination."""
    if db.get_bind().dialect.name == "postgresql":
        transaction_ids = cast(audit_event.after_state, JSONB)["transaction_ids"]
        return func.jsonb_array_elements_text(transaction_ids).table_valued("value")
    return func.json_each(audit_event.after_state, "$.transaction_ids").table_valued("key", "value")


def case_creation_parent_ids_query(db, case_id: str):
    """Select follow-up parent IDs without loading the creation event's linked transaction array."""
    created_event = aliased(AuditEvent)
    parent_id = audit_json_text_value(db, created_event, "parent_case_id")
    return select(parent_id).where(
        created_event.case_id == case_id,
        created_event.event_type == "CASE_CREATED",
        parent_id.is_not(None),
        audit_json_value_is_string(db, created_event, "parent_case_id"),
    )


def linked_follow_up_child_ids_query(db, parent_id: str):
    """Select historical child IDs from follow-up events without expanding them in Python."""
    follow_up_event = aliased(AuditEvent)
    child_id = audit_json_text_value(db, follow_up_event, "follow_up_id")
    return select(child_id).where(
        follow_up_event.case_id == parent_id,
        follow_up_event.event_type == "CASE_FOLLOW_UP_LINKED",
        child_id.is_not(None),
        audit_json_value_is_string(db, follow_up_event, "follow_up_id"),
    )


def audit_json_text_value(db, audit_event, key: str):
    """Read a scalar JSON property as text across the supported database dialects."""
    if db.get_bind().dialect.name == "postgresql":
        return cast(audit_event.after_state, JSONB).op("->>", return_type=String)(key)
    return func.json_extract(audit_event.after_state, f"$.{key}")


def audit_json_value_is_string(db, audit_event, key: str):
    """Keep malformed numeric and object identifiers out of lineage results."""
    if db.get_bind().dialect.name == "postgresql":
        return (
            func.jsonb_typeof(cast(audit_event.after_state, JSONB).op("->", return_type=JSONB)(key))
            == "string"
        )
    return func.json_type(audit_event.after_state, f"$.{key}") == "text"


def audit_json_boolean_is(db, audit_event, key: str, value: bool):
    """Compare a JSON boolean consistently across PostgreSQL and SQLite."""
    if db.get_bind().dialect.name == "postgresql":
        text_value = cast(audit_event.after_state, JSONB).op("->>", return_type=String)(key)
        return text_value == str(value).lower()
    return func.json_extract(audit_event.after_state, f"$.{key}").is_(value)
