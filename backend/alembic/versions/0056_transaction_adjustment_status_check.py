"""Restrict correction review states to the supported state machine."""
from alembic import op


revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("transaction_adjustments") as batch_op:
        batch_op.create_check_constraint(
            "ck_transaction_adjustments_status",
            "status IN ('Proposed', 'Approved', 'Rejected')",
        )


def downgrade():
    with op.batch_alter_table("transaction_adjustments") as batch_op:
        batch_op.drop_constraint("ck_transaction_adjustments_status", type_="check")
