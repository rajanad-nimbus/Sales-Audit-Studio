"""Allow POS Day Close markers to be required for store-day completion."""

import sqlalchemy as sa
from alembic import op

revision = "0108"
down_revision = "0107"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "source_settings",
        sa.Column("require_day_close", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade():
    op.drop_column("source_settings", "require_day_close")
