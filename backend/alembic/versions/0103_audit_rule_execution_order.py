"""Configure audit rule execution order and stop behavior."""

import sqlalchemy as sa
from alembic import op

revision = "0103"
down_revision = "0102"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "configured_audit_rules",
        sa.Column("execution_order", sa.Integer(), nullable=False, server_default="100"),
    )
    op.add_column(
        "configured_audit_rules",
        sa.Column("stop_processing", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("configured_audit_rules", "stop_processing")
    op.drop_column("configured_audit_rules", "execution_order")
