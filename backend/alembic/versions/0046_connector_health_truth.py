"""Represent unconfigured connector adapters without claiming health."""

import sqlalchemy as sa
from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None

BUILT_IN_CONNECTORS = ("POS Feed", "Payment Processor", "Bank Feed", "ERP / GL")


def upgrade():
    with op.batch_alter_table("connectors") as batch_op:
        batch_op.alter_column(
            "last_checked_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=True,
        )
    op.execute(
        sa.text(
            "UPDATE connectors SET state = 'Unknown', mode = 'Not Configured', last_checked_at = NULL "
            "WHERE name IN ('POS Feed', 'Payment Processor', 'Bank Feed', 'ERP / GL')"
        )
    )


def downgrade():
    # The upgrade intentionally discarded unverified health claims. Do not invent
    # a successful check while restoring the old NOT NULL schema constraint.
    op.execute(
        sa.text(
            "UPDATE connectors SET last_checked_at = CURRENT_TIMESTAMP WHERE last_checked_at IS NULL"
        )
    )
    with op.batch_alter_table("connectors") as batch_op:
        batch_op.alter_column(
            "last_checked_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )
