"""Per-source switches for the daily batch and manual sync."""

import sqlalchemy as sa
from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "source_settings",
        sa.Column("source_system", sa.String(32), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("in_daily_batch", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("manual_sync", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_by", sa.String(255), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade():
    op.drop_table("source_settings")
