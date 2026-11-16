"""Preserve retailer-defined transaction reference fields."""

import sqlalchemy as sa
from alembic import op

revision = "0094"
down_revision = "0093"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "canonical_transactions",
        sa.Column("user_references", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("canonical_transactions", "user_references")
