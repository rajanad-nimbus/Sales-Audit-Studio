"""Track source changes made after store-day totals were calculated."""

import sqlalchemy as sa
from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "store_days",
        sa.Column("totals_stale", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade():
    op.drop_column("store_days", "totals_stale")
