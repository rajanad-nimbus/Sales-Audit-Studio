"""Index source identities for idempotent ingestion checks."""

from alembic import op


revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


def upgrade():
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_source_records_system_record_id",
            "source_records",
            ["source_system", "source_record_id"],
            postgresql_concurrently=True,
        )


def downgrade():
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_source_records_system_record_id",
            table_name="source_records",
            postgresql_concurrently=True,
        )
