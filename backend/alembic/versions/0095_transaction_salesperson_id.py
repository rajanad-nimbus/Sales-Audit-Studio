"""Retain transaction-level salesperson attribution."""

import sqlalchemy as sa
from alembic import op

revision = "0095"
down_revision = "0094"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "canonical_transactions",
        sa.Column("salesperson_id", sa.String(length=100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("canonical_transactions", "salesperson_id")
