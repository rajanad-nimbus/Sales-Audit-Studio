"""Associate existing monetary control thresholds with USD."""
import sqlalchemy as sa
from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("reconciliation_policies", sa.Column("amount_tolerance_currency", sa.String(3), nullable=False, server_default="USD"))
    op.add_column("configured_audit_rules", sa.Column("threshold_currency", sa.String(3), nullable=True, server_default="USD"))


def downgrade():
    op.drop_column("configured_audit_rules", "threshold_currency")
    op.drop_column("reconciliation_policies", "amount_tolerance_currency")
