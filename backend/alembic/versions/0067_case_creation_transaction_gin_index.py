"""Index transaction IDs recorded on case creation audit events."""

from alembic import op


revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None


INDEX_NAME = "ix_audit_case_created_transaction_ids"


def upgrade():
    with op.get_context().autocommit_block():
        op.execute(
            f"CREATE INDEX CONCURRENTLY {INDEX_NAME} ON audit_events "
            "USING gin ((after_state::jsonb -> 'transaction_ids')) "
            "WHERE event_type = 'CASE_CREATED'"
        )


def downgrade():
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY {INDEX_NAME}")
