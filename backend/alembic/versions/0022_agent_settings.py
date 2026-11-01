"""admin-controlled agent settings
Revision ID: 0022
Revises: 0021
"""

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agent_settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.JSON, nullable=False),
        sa.Column("updated_by", sa.String(255), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("agent_settings")
