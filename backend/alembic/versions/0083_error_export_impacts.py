"""Add per-error downstream export impact configuration."""

import sqlalchemy as sa
from alembic import op

revision = "0083"
down_revision = "0082"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "sales_audit_error_definitions",
        sa.Column("export_impact_configured", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "sales_audit_error_definitions",
        sa.Column("export_block_destinations", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )


def downgrade():
    op.drop_column("sales_audit_error_definitions", "export_block_destinations")
    op.drop_column("sales_audit_error_definitions", "export_impact_configured")
