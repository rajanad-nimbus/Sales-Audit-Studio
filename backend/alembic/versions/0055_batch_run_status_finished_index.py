"""Index the latest successful batch status lookup."""
from alembic import op

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "ix_batch_runs_status_finished_at",
        "batch_runs",
        ["status", "finished_at"],
    )


def downgrade():
    op.drop_index("ix_batch_runs_status_finished_at", table_name="batch_runs")
