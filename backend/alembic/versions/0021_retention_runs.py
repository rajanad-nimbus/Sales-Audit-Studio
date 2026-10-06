"""retention archive run history
Revision ID: 0021
Revises: 0020
"""
from alembic import op
import sqlalchemy as sa
revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None
def upgrade():
 op.create_table("retention_runs",sa.Column("id",sa.String(36),primary_key=True),sa.Column("dataset",sa.String(50),nullable=False),sa.Column("retain_days",sa.Integer,nullable=False),sa.Column("eligible_records",sa.Integer,nullable=False),sa.Column("archived_records",sa.Integer,nullable=False),sa.Column("purged_records",sa.Integer,nullable=False),sa.Column("archive_location",sa.Text),sa.Column("archive_hash",sa.String(64)),sa.Column("status",sa.String(24),nullable=False),sa.Column("error",sa.Text),sa.Column("performed_by",sa.String(255),nullable=False),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False));op.create_index("ix_retention_runs_dataset","retention_runs",["dataset"])
def downgrade(): op.drop_table("retention_runs")
