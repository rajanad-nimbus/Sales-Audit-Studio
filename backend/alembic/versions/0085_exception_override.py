"""Add maker-checker override history to sales audit exceptions."""

import sqlalchemy as sa
from alembic import op

revision = "0085"
down_revision = "0084"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("exceptions", sa.Column("override_reason", sa.Text(), nullable=True))
    op.add_column("exceptions", sa.Column("override_requested_by", sa.String(255), nullable=True))
    op.add_column(
        "exceptions", sa.Column("override_requested_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("exceptions", sa.Column("override_approved_by", sa.String(255), nullable=True))
    op.add_column(
        "exceptions", sa.Column("override_approved_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade():
    op.drop_column("exceptions", "override_approved_at")
    op.drop_column("exceptions", "override_approved_by")
    op.drop_column("exceptions", "override_requested_at")
    op.drop_column("exceptions", "override_requested_by")
    op.drop_column("exceptions", "override_reason")
