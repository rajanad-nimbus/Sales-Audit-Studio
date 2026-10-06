"""source feed profiles

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "source_feed_profiles",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False, unique=True),
        sa.Column("source_system", sa.String(length=32), nullable=False),
        sa.Column("schedule", sa.String(length=128), nullable=True),
        sa.Column("column_mapping", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_source_feed_profiles_source_system", "source_feed_profiles", ["source_system"])


def downgrade() -> None:
    op.drop_index("ix_source_feed_profiles_source_system", table_name="source_feed_profiles")
    op.drop_table("source_feed_profiles")
