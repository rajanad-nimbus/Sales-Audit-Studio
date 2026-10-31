"""Index case exceptions for bounded follow-up keyset reads."""

from alembic import op


revision = "0070"
down_revision = "0069"
branch_labels = None
depends_on = None


INDEX_NAME = "ix_exceptions_case_id_id"


def upgrade():
    if op.get_bind().dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        op.create_index(
            INDEX_NAME,
            "exceptions",
            ["case_id", "id"],
            postgresql_concurrently=True,
        )


def downgrade():
    if op.get_bind().dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        op.drop_index(
            INDEX_NAME,
            table_name="exceptions",
            postgresql_concurrently=True,
        )
