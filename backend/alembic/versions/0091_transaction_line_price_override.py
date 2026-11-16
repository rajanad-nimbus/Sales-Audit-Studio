"""Retain item price override evidence from POS feeds."""

import sqlalchemy as sa
from alembic import op

revision = "0091"
down_revision = "0090"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "transaction_lines",
        sa.Column("original_unit_price", sa.Numeric(15, 2), nullable=True),
    )
    op.add_column(
        "transaction_lines",
        sa.Column("price_override_reason", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("transaction_lines", "price_override_reason")
    op.drop_column("transaction_lines", "original_unit_price")
