"""Index source records by delivery for pipeline history lookups."""

from alembic import op


revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def upgrade():
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_source_records_system_delivery",
            "source_records",
            ["source_system", "delivery_id"],
            postgresql_concurrently=True,
        )


def downgrade():
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_source_records_system_delivery",
            table_name="source_records",
            postgresql_concurrently=True,
        )
