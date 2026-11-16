"""Add effective date windows to configured Sales Audit rules."""

import sqlalchemy as sa
from alembic import op

revision = "0101"
down_revision = "0100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("configured_audit_rules", sa.Column("start_date", sa.String(10), nullable=True))
    op.add_column("configured_audit_rules", sa.Column("end_date", sa.String(10), nullable=True))


def downgrade() -> None:
    op.drop_column("configured_audit_rules", "end_date")
    op.drop_column("configured_audit_rules", "start_date")
