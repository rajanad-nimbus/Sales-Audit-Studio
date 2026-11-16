"""Persist the configured sales-audit export unit of work."""

import sqlalchemy as sa
from alembic import op

revision = "0082"
down_revision = "0081"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "system_options",
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("value", sa.String(255), nullable=False),
        sa.Column("updated_by", sa.String(255)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("system_options")
