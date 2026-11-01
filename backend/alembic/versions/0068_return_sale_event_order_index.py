"""Order sale candidates for store-scoped return linking."""

from alembic import op

revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None


def upgrade():
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_ct_store_type_event_id",
            "canonical_transactions",
            ["store_id", "transaction_type", "event_timestamp", "id"],
            postgresql_concurrently=True,
        )


def downgrade():
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_ct_store_type_event_id",
            table_name="canonical_transactions",
            postgresql_concurrently=True,
        )
