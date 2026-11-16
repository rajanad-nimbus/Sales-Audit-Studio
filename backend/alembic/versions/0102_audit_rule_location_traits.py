"""Scope configured Sales Audit rules to location traits."""

import sqlalchemy as sa
from alembic import op

revision = "0102"
down_revision = "0101"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "configured_audit_rules",
        sa.Column("location_traits", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("configured_audit_rules", "location_traits")
