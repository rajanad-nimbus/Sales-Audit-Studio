"""Track automatic Store Day opening outcomes for scheduled batches."""

import sqlalchemy as sa
from alembic import op

revision = "0089"
down_revision = "0088"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("batch_runs", sa.Column("store_day_open_status", sa.String(20), nullable=True))
    op.add_column(
        "batch_runs",
        sa.Column("store_days_opened", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "batch_runs",
        sa.Column("store_days_already_open", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("batch_runs", sa.Column("store_day_open_error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("batch_runs", "store_day_open_error")
    op.drop_column("batch_runs", "store_days_already_open")
    op.drop_column("batch_runs", "store_days_opened")
    op.drop_column("batch_runs", "store_day_open_status")
