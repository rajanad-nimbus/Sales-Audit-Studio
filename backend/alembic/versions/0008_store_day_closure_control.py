"""store day closure approval control

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("store_days", sa.Column("closed_by", sa.String(255)))
    op.add_column("store_days", sa.Column("reopened_by", sa.String(255)))
    op.create_table("store_day_closure_requests", sa.Column("id", sa.String(36), primary_key=True), sa.Column("store_day_id", sa.String(36), sa.ForeignKey("store_days.id"), nullable=False), sa.Column("requested_by", sa.String(255), nullable=False), sa.Column("evidence_basis", sa.Text(), nullable=False), sa.Column("status", sa.String(24), nullable=False), sa.Column("reviewed_by", sa.String(255)), sa.Column("reviewed_at", sa.DateTime(timezone=True)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_store_day_closure_requests_store_day_id", "store_day_closure_requests", ["store_day_id"])
    op.create_index("ix_store_day_closure_requests_status", "store_day_closure_requests", ["status"])

def downgrade():
    op.drop_table("store_day_closure_requests"); op.drop_column("store_days", "reopened_by"); op.drop_column("store_days", "closed_by")
