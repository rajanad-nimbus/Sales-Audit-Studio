"""Enforce one active recommendation for each case."""
from alembic import context, op
import sqlalchemy as sa


revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


INDEX_NAME = "uq_recommendations_one_proposed_per_case"


def upgrade():
    if not context.is_offline_mode():
        conflicts = op.get_bind().execute(sa.text(
            "SELECT case_id FROM recommendations WHERE status = 'Proposed' "
            "GROUP BY case_id HAVING COUNT(*) > 1 ORDER BY case_id"
        )).scalars().all()
        if conflicts:
            case_ids = ", ".join(conflicts)
            raise RuntimeError(
                "Cannot enforce one Proposed recommendation per case; resolve duplicate "
                f"recommendations for case IDs: {case_ids}"
            )
    op.create_index(
        INDEX_NAME,
        "recommendations",
        ["case_id"],
        unique=True,
        postgresql_where=sa.text("status = 'Proposed'"),
        sqlite_where=sa.text("status = 'Proposed'"),
    )


def downgrade():
    op.drop_index(INDEX_NAME, table_name="recommendations")
