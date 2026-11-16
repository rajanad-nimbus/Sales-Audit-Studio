"""Retain source-specific POS item detail fields."""

import sqlalchemy as sa
from alembic import op

revision = "0093"
down_revision = "0092"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "transaction_lines",
        sa.Column("source_attributes", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("transaction_lines", "source_attributes")
