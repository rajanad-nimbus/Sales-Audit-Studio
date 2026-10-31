"""Index the paginated transaction-adjustment queue."""

from alembic import op


revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


INDEXES = (
    ("ix_transaction_adjustments_created_id", ["created_at", "id"]),
    ("ix_transaction_adjustments_status_created_id", ["status", "created_at", "id"]),
)


def upgrade():
    with op.get_context().autocommit_block():
        for name, columns in INDEXES:
            op.create_index(name, "transaction_adjustments", columns, postgresql_concurrently=True)


def downgrade():
    with op.get_context().autocommit_block():
        for name, _columns in reversed(INDEXES):
            op.drop_index(name, table_name="transaction_adjustments", postgresql_concurrently=True)
