"""Make the large-gap thresholds a reconciliation policy setting."""

import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "reconciliation_policies",
        sa.Column("gap_min_count", sa.Integer(), nullable=False, server_default="10"),
    )
    op.add_column(
        "reconciliation_policies",
        sa.Column("gap_share", sa.Numeric(5, 4), nullable=False, server_default="0.1"),
    )


def downgrade():
    op.drop_column("reconciliation_policies", "gap_share")
    op.drop_column("reconciliation_policies", "gap_min_count")
