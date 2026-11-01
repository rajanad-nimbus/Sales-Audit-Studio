"""Persist idempotency receipts for the built-in test receiver."""
import sqlalchemy as sa
from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("test_sink_receipts",
        sa.Column("idempotency_key", sa.String(64), primary_key=True),
        sa.Column("dataset", sa.String(30), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("byte_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table("test_sink_receipts")
