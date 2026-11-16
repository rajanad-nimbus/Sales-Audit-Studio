"""Preserve the source customer reference on canonical transactions."""

import sqlalchemy as sa
from alembic import op

revision = "0092"
down_revision = "0091"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "canonical_transactions",
        sa.Column("customer_id", sa.String(length=100), nullable=True),
    )
    op.create_index(
        "ix_canonical_transactions_customer_id",
        "canonical_transactions",
        ["customer_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_canonical_transactions_customer_id", table_name="canonical_transactions")
    op.drop_column("canonical_transactions", "customer_id")
