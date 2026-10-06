"""configurable reconciliation tolerance policy

Revision ID: 0010
Revises: 0009
"""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("reconciliation_policies", sa.Column("id", sa.String(36), primary_key=True), sa.Column("name", sa.String(100), nullable=False, unique=True), sa.Column("amount_tolerance", sa.Numeric(15,2), nullable=False), sa.Column("settlement_day_tolerance", sa.Integer(), nullable=False), sa.Column("active", sa.Boolean(), nullable=False), sa.Column("updated_by", sa.String(255)), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))

def downgrade():
    op.drop_table("reconciliation_policies")
