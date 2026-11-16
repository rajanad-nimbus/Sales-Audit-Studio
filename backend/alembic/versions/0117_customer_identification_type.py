"""Retain the source type for a transaction customer identifier."""

import sqlalchemy as sa
from alembic import op

revision = "0117"
down_revision = "0116"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "canonical_transactions",
        sa.Column("customer_identification_type", sa.String(length=50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("canonical_transactions", "customer_identification_type")
