"""Add worksheet and approval lifecycle to configured audit rules."""

import sqlalchemy as sa
from alembic import op

revision = "0084"
down_revision = "0083"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "configured_audit_rules",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "configured_audit_rules",
        sa.Column("status", sa.String(24), nullable=False, server_default="Active"),
    )
    op.add_column(
        "configured_audit_rules",
        sa.Column("submitted_by", sa.String(255), nullable=True),
    )
    op.add_column(
        "configured_audit_rules",
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "configured_audit_rules",
        sa.Column("approved_by", sa.String(255), nullable=True),
    )
    op.add_column(
        "configured_audit_rules",
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE configured_audit_rules SET status = "
            "CASE WHEN enabled THEN 'Active' ELSE 'Disabled' END"
        )
    )


def downgrade():
    for column in (
        "approved_at",
        "approved_by",
        "submitted_at",
        "submitted_by",
        "status",
        "revision",
    ):
        op.drop_column("configured_audit_rules", column)
