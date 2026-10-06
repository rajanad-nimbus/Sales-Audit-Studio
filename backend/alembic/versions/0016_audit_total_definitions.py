"""retailer configured audit totals

Revision ID: 0016
Revises: 0015
"""
from alembic import op
import sqlalchemy as sa
revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None
def upgrade():
    op.create_table("audit_total_definitions", sa.Column("id", sa.String(36), primary_key=True), sa.Column("name", sa.String(100), nullable=False, unique=True), sa.Column("aggregation", sa.String(16), nullable=False), sa.Column("source_system", sa.String(50)), sa.Column("transaction_types", sa.JSON(), nullable=False), sa.Column("tender_type", sa.String(50)), sa.Column("level", sa.String(24), nullable=False), sa.Column("active", sa.Boolean(), nullable=False), sa.Column("updated_by", sa.String(255)), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
def downgrade(): op.drop_table("audit_total_definitions")
