"""Index transaction lookups used by case evidence retrieval."""

from alembic import op


revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None


def upgrade():
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_ct_store_business_date_type",
            "canonical_transactions",
            ["store_id", "business_date", "transaction_type"],
            postgresql_concurrently=True,
        )


def downgrade():
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_ct_store_business_date_type",
            table_name="canonical_transactions",
            postgresql_concurrently=True,
        )
