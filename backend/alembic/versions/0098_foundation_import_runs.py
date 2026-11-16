"""Track foundation CSV import processes and validation outcomes."""

import sqlalchemy as sa
from alembic import op

revision = "0098"
down_revision = "0097"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "foundation_import_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("process_name", sa.String(length=100), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("categories", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("rows_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unchanged_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("errors", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("uploaded_by", sa.String(length=255), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_foundation_import_runs_status", "foundation_import_runs", ["status"])
    op.create_index(
        "ix_foundation_import_runs_started_at", "foundation_import_runs", ["started_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_foundation_import_runs_started_at", table_name="foundation_import_runs")
    op.drop_index("ix_foundation_import_runs_status", table_name="foundation_import_runs")
    op.drop_table("foundation_import_runs")
