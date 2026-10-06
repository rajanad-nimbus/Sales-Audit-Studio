"""store day lifecycle and audit totals

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("store_days", sa.Column("id", sa.String(36), primary_key=True), sa.Column("store_id", sa.String(50), nullable=False), sa.Column("business_date", sa.String(10), nullable=False), sa.Column("status", sa.String(32), nullable=False), sa.Column("audit_version", sa.Integer(), nullable=False), sa.Column("closed_at", sa.DateTime(timezone=True)), sa.Column("reopened_reason", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("store_id", "business_date", name="uq_store_day"))
    op.create_index("ix_store_days_store_id", "store_days", ["store_id"]); op.create_index("ix_store_days_business_date", "store_days", ["business_date"]); op.create_index("ix_store_days_status", "store_days", ["status"])
    op.create_table("audit_totals", sa.Column("id", sa.String(36), primary_key=True), sa.Column("store_day_id", sa.String(36), sa.ForeignKey("store_days.id"), nullable=False), sa.Column("audit_version", sa.Integer(), nullable=False), sa.Column("total_name", sa.String(100), nullable=False), sa.Column("level", sa.String(24), nullable=False), sa.Column("dimension_key", sa.String(100)), sa.Column("calculated_amount", sa.Numeric(15,2), nullable=False), sa.Column("declared_amount", sa.Numeric(15,2)), sa.Column("variance_amount", sa.Numeric(15,2)), sa.Column("currency", sa.String(3), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_audit_totals_store_day_id", "audit_totals", ["store_day_id"]); op.create_index("ix_audit_totals_audit_version", "audit_totals", ["audit_version"])

def downgrade():
    op.drop_table("audit_totals"); op.drop_table("store_days")
