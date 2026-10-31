"""Allow at most one active daily batch per business date."""
from alembic import context, op
import sqlalchemy as sa

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade():
    if not context.is_offline_mode():
        conflicts = op.get_bind().execute(sa.text(
            "SELECT business_date FROM batch_runs WHERE status = 'Running' "
            "GROUP BY business_date HAVING COUNT(*) > 1 ORDER BY business_date"
        )).scalars().all()
        if conflicts:
            dates = ", ".join(conflicts)
            raise RuntimeError(
                "Cannot enforce one active batch per business date; resolve duplicate Running "
                f"batch_runs for: {dates}"
            )
    op.create_index(
        "uq_batch_runs_one_running_per_date",
        "batch_runs",
        ["business_date"],
        unique=True,
        postgresql_where=sa.text("status = 'Running'"),
        sqlite_where=sa.text("status = 'Running'"),
    )


def downgrade():
    op.drop_index("uq_batch_runs_one_running_per_date", table_name="batch_runs")
