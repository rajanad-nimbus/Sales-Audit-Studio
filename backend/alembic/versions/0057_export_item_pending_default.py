"""Align the database export-item default with the ORM state machine."""
import sqlalchemy as sa
from alembic import op

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "export_items",
        "state",
        existing_type=sa.String(length=20),
        existing_nullable=False,
        server_default="Pending",
    )


def downgrade():
    op.alter_column(
        "export_items",
        "state",
        existing_type=sa.String(length=20),
        existing_nullable=False,
        server_default="Delivered",
    )
