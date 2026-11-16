"""Support register and cashier audit-rule balancing."""

import sqlalchemy as sa
from alembic import op

revision = "0104"
down_revision = "0103"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "configured_audit_rules",
        sa.Column("balance_level", sa.String(length=24), nullable=False, server_default="Store"),
    )
    op.add_column("exceptions", sa.Column("balance_level", sa.String(length=24), nullable=True))
    op.add_column(
        "exceptions", sa.Column("balance_dimension", sa.String(length=100), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("exceptions", "balance_dimension")
    op.drop_column("exceptions", "balance_level")
    op.drop_column("configured_audit_rules", "balance_level")
