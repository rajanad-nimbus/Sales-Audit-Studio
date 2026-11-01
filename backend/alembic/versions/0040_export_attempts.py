"""Fence export delivery attempts and permit recovery of interrupted workers."""
import sqlalchemy as sa
from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("export_batches", sa.Column("delivery_attempt_id", sa.String(36), nullable=True))
    op.add_column("export_batches", sa.Column("attempt_started_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(sa.text("UPDATE export_batches SET attempt_started_at = created_at WHERE status = 'Pending'"))


def downgrade():
    op.drop_column("export_batches", "attempt_started_at")
    op.drop_column("export_batches", "delivery_attempt_id")
