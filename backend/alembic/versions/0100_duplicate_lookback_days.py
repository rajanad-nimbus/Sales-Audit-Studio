"""Configure the POS duplicate-sales comparison window."""

import sqlalchemy as sa
from alembic import op

revision = "0100"
down_revision = "0099"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reconciliation_policies",
        sa.Column("duplicate_lookback_days", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("reconciliation_policies", "duplicate_lookback_days")
