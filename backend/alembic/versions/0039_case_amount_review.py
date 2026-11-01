"""Track case amounts requiring review after source corrections."""
import sqlalchemy as sa
from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("cases", sa.Column("amount_needs_review", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute(sa.text("UPDATE cases SET amount_needs_review = true WHERE EXISTS "
        "(SELECT 1 FROM audit_events stale WHERE stale.case_id = cases.id "
        "AND stale.event_type = 'CASE_EVIDENCE_STALE' AND NOT EXISTS "
        "(SELECT 1 FROM audit_events reviewed WHERE reviewed.case_id = cases.id "
        "AND reviewed.event_type = 'CASE_AMOUNTS_REVIEWED' AND reviewed.created_at >= stale.created_at))"))


def downgrade():
    op.drop_column("cases", "amount_needs_review")
