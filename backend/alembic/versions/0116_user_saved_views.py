"""Persist named worklist views by verified user."""

import sqlalchemy as sa
from alembic import op

revision = "0116"
down_revision = "0115"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_saved_views",
        sa.Column("user_identifier", sa.String(255), primary_key=True),
        sa.Column("scope", sa.String(40), primary_key=True),
        sa.Column("views", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade():
    op.drop_table("user_saved_views")
