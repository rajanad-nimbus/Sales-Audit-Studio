"""Store an operator between every operand in combined total formulas."""

import sqlalchemy as sa
from alembic import op

revision = "0087"
down_revision = "0086"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "audit_total_definitions",
        sa.Column(
            "combined_total_operators",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )


def downgrade():
    op.drop_column("audit_total_definitions", "combined_total_operators")
