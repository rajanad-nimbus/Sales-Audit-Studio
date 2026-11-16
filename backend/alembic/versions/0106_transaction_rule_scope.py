"""Add Store Day absence evaluation to transaction audit rules."""

import sqlalchemy as sa
from alembic import op

revision = "0106"
down_revision = "0105"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "configured_transaction_audit_rules",
        sa.Column(
            "evaluation_scope",
            sa.String(length=24),
            nullable=False,
            server_default="Transaction",
        ),
    )


def downgrade() -> None:
    op.drop_column("configured_transaction_audit_rules", "evaluation_scope")
