"""Allow configured audit rules to choose their threshold comparison."""

import sqlalchemy as sa
from alembic import op

revision = "0086"
down_revision = "0085"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "configured_audit_rules",
        sa.Column("comparison", sa.String(16), nullable=False, server_default="ABS_GT"),
    )


def downgrade():
    op.drop_column("configured_audit_rules", "comparison")
