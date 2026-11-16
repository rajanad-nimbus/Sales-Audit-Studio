"""Retain RTLOG paid-out vendor and reference details."""

import sqlalchemy as sa
from alembic import op

revision = "0090"
down_revision = "0089"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "register_events",
        sa.Column("details", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )


def downgrade() -> None:
    op.drop_column("register_events", "details")
