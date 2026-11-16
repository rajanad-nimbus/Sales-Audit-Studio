"""Persist total roll-up identity separately from balancing dimensions."""

import sqlalchemy as sa
from alembic import op

revision = "0107"
down_revision = "0106"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("audit_totals", sa.Column("rollup_key", sa.String(length=2048), nullable=True))
    op.add_column(
        "variance_explanations", sa.Column("rollup_key", sa.String(length=2048), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("variance_explanations", "rollup_key")
    op.drop_column("audit_totals", "rollup_key")
