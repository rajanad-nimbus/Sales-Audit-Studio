from apps.cases.lineage import (
    audit_json_boolean_is,
    case_creation_has_transactions,
    case_creation_mentions_transaction,
    case_creation_parent_ids_query,
    case_transaction_ids_table,
    linked_follow_up_child_ids_query,
)
from models import AuditEvent
from sqlalchemy import select, true
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import aliased


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
        case_creation_has_transactions(PostgreSQLSession(), event),
        case_creation_mentions_transaction(PostgreSQLSession(), "transaction-123", event),
    )
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)

    assert "jsonb_array_elements_text" in sql
    assert "jsonb_array_length" in sql
    assert " IN (" not in sql.upper()


def test_postgres_follow_up_lineage_extracts_ids_without_loading_creation_payloads():
    class PostgreSQLSession:
        class Bind:
            class Dialect:
                name = "postgresql"

            dialect = Dialect()

        @staticmethod
        def get_bind():
            return PostgreSQLSession.Bind()

    parent_query = case_creation_parent_ids_query(PostgreSQLSession(), "child")
    children_query = linked_follow_up_child_ids_query(PostgreSQLSession(), "parent")
    parent_sql = str(parent_query.compile(dialect=postgresql.dialect()))
    children_sql = str(children_query.compile(dialect=postgresql.dialect()))

    assert "->>" in parent_sql and "jsonb_typeof" in parent_sql
    assert "->>" in children_sql and "jsonb_typeof" in children_sql
    assert "transaction_ids" not in parent_sql


def test_sqlite_follow_up_lineage_ignores_non_string_ids():
    class SQLiteSession:
        class Bind:
            class Dialect:
                name = "sqlite"

            dialect = Dialect()

        @staticmethod
        def get_bind():
            return SQLiteSession.Bind()

    children_query = linked_follow_up_child_ids_query(SQLiteSession(), "parent")
    sql = str(children_query.compile())

    assert "json_type" in sql
    assert "text" in children_query.compile().params.values()


def test_postgres_json_boolean_comparison_uses_text_boolean_value():
    class PostgreSQLSession:
        class Bind:
            class Dialect:
                name = "postgresql"

            dialect = Dialect()

        @staticmethod
        def get_bind():
            return PostgreSQLSession.Bind()

    statement = select(AuditEvent.id).where(
        audit_json_boolean_is(PostgreSQLSession(), AuditEvent, "case_closed", False)
    )
    compiled = statement.compile(dialect=postgresql.dialect())

    assert "->>" in str(compiled)
    assert "false" in compiled.params.values()
