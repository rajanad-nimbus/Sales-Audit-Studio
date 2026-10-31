from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from apps.cases.lineage import case_creation_mentions_transaction
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
