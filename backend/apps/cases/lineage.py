"""Database predicates for querying case-to-transaction audit lineage."""

from sqlalchemy import String, cast, literal_column
from sqlalchemy.dialects.postgresql import JSONB

from models import AuditEvent


def case_creation_mentions_transaction(db, transaction_id: str):
    """Match a transaction ID as an array element, using the indexed PostgreSQL path."""
    if db.get_bind().dialect.name == "postgresql":
        transaction_ids = cast(AuditEvent.after_state, JSONB).op(
            "->", return_type=JSONB,
        )(literal_column("'transaction_ids'"))
        return transaction_ids.contains([transaction_id])
    # SQLite is used by the unit test suite; Python still validates exact array membership.
    return cast(AuditEvent.after_state, String).contains(transaction_id, autoescape=True)
