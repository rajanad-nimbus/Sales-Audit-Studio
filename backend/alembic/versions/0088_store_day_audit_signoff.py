"""Persist auditor approval against a specific store-day totals version."""

import sqlalchemy as sa
from alembic import op

revision = "0088"
down_revision = "0087"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("store_days", sa.Column("audit_signed_version", sa.Integer(), nullable=True))
    op.add_column("store_days", sa.Column("audit_signed_by", sa.String(length=255), nullable=True))
    op.add_column(
        "store_days", sa.Column("audit_signed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "store_days", sa.Column("audit_signoff_rationale", sa.Text(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("store_days", "audit_signoff_rationale")
    op.drop_column("store_days", "audit_signed_at")
    op.drop_column("store_days", "audit_signed_by")
    op.drop_column("store_days", "audit_signed_version")
