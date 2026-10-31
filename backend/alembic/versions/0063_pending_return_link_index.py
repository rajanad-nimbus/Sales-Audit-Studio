"""Index unresolved POS return references for targeted ingest linking."""

from alembic import op
import sqlalchemy as sa


revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


def upgrade():
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_ct_pending_return_link",
            "canonical_transactions",
            ["store_id", "original_reference", "id"],
            postgresql_where=sa.text("transaction_type = 'Return' AND original_transaction_id IS NULL"),
            postgresql_concurrently=True,
        )


def downgrade():
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_ct_pending_return_link",
            table_name="canonical_transactions",
            postgresql_concurrently=True,
        )
