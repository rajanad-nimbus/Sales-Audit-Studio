"""Retain safe POS-specific tender audit fields."""

import sqlalchemy as sa
from alembic import op

revision = "0097"
down_revision = "0096"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "transaction_tenders",
        sa.Column("source_attributes", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("transaction_tenders", "source_attributes")
