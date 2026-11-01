"""Index archived manifests for bounded reconciliation paging."""

from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "ix_source_records_system_status_id",
        "source_records",
        ["source_system", "status", "id"],
    )


def downgrade():
    op.drop_index("ix_source_records_system_status_id", table_name="source_records")
