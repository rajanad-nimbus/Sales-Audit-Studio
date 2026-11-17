"""Store normalized paid-out references on canonical transactions."""

import sqlalchemy as sa
from alembic import op

revision = "0123"
down_revision = "0122"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("canonical_transactions", sa.Column("paid_out_details", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("canonical_transactions", "paid_out_details")
