"""Constrain manual reconciliation matches to supported review states."""

from alembic import op

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade():
    op.create_check_constraint(
        "ck_reconciliation_matches_status",
        "reconciliation_matches",
        "status IN ('Proposed', 'Approved', 'Rejected')",
    )


def downgrade():
    op.drop_constraint(
        "ck_reconciliation_matches_status",
        "reconciliation_matches",
        type_="check",
    )
