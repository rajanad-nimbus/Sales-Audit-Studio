"""Add Flash report usages to total definitions."""

import sqlalchemy as sa
from alembic import op

revision = "0122"
down_revision = "0121"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "audit_total_definitions",
        sa.Column("report_usages", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )


def downgrade():
    op.drop_column("audit_total_definitions", "report_usages")
