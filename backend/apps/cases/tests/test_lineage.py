from sqlalchemy import select, true
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import aliased

from apps.cases.lineage import (
    case_creation_has_transactions,
    case_creation_mentions_transaction,
    case_transaction_ids_table,
)
from models import AuditEvent


def test_postgres_lineage_predicate_uses_exact_indexable_json_array_containment():
    class PostgreSQLSession:
        class Bind:
            class Dialect:
                name = "postgresql"

            dialect = Dialect()

        @staticmethod
        def get_bind():
            return PostgreSQLSession.Bind()

    statement = select(AuditEvent.id).where(
        case_creation_mentions_transaction(PostgreSQLSession(), "transaction-123")
    )
    compiled = statement.compile(dialect=postgresql.dialect())

    assert "@>" in str(compiled)
    assert "-> 'transaction_ids'" in str(compiled)
    assert ["transaction-123"] in compiled.params.values()


def test_postgres_case_lineage_pages_through_json_array_rows():
    class PostgreSQLSession:
        class Bind:
            class Dialect:
                name = "postgresql"

            dialect = Dialect()

        @staticmethod
        def get_bind():
            return PostgreSQLSession.Bind()

    event = aliased(AuditEvent)
    transaction_ids = case_transaction_ids_table(PostgreSQLSession(), event)
    statement = select(event.id).select_from(event).join(transaction_ids, true()).where(
        case_creation_has_transactions(PostgreSQLSession(), event)
    )
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)

    assert "jsonb_array_elements_text" in sql
    assert "jsonb_array_length" in sql
    assert " IN (" not in sql.upper()
