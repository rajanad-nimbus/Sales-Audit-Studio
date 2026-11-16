"""Persist personal regional and accessibility preferences."""

import sqlalchemy as sa
from alembic import op

revision = "0109"
down_revision = "0108"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_preferences",
        sa.Column("user_identifier", sa.String(length=255), primary_key=True),
        sa.Column("locale", sa.String(length=20), nullable=False, server_default="en-US"),
        sa.Column("time_zone", sa.String(length=64), nullable=False, server_default="UTC"),
        sa.Column("density", sa.String(length=16), nullable=False, server_default="comfortable"),
        sa.Column("contrast", sa.String(length=16), nullable=False, server_default="standard"),
        sa.Column("motion", sa.String(length=16), nullable=False, server_default="system"),
        sa.Column("theme", sa.String(length=16), nullable=False, server_default="system"),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade():
    op.drop_table("user_preferences")
