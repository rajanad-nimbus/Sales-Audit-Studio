"""Allow administrators to manage verified users' store scopes."""

import sqlalchemy as sa
from alembic import op

revision = "0096"
down_revision = "0095"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "store_assignments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_identifier", sa.String(length=255), nullable=False),
        sa.Column("store_ids", sa.JSON(), nullable=False),
        sa.Column("updated_by", sa.String(length=255), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_identifier"),
    )

def downgrade() -> None:
    op.drop_table("store_assignments")
