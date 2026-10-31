"""Track active batch progress for safe abandoned-run recovery."""
from alembic import op
import sqlalchemy as sa


revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("batch_runs", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_batch_runs_heartbeat_at", "batch_runs", ["heartbeat_at"])


def downgrade():
    op.drop_index("ix_batch_runs_heartbeat_at", table_name="batch_runs")
    op.drop_column("batch_runs", "heartbeat_at")
