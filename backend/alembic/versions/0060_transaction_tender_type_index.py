"""Index tender facets and transaction tender filtering."""
from alembic import op


revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade():
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_canonical_transactions_tender_type",
            "canonical_transactions",
            ["tender_type"],
            postgresql_concurrently=True,
        )


def downgrade():
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_canonical_transactions_tender_type",
            table_name="canonical_transactions",
            postgresql_concurrently=True,
        )
