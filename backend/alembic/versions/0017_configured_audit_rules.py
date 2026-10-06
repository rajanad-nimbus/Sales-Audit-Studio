"""configured audit rules

Revision ID: 0017
Revises: 0016
"""
from alembic import op
import sqlalchemy as sa
revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None
def upgrade():
    op.create_table("configured_audit_rules", sa.Column("id", sa.String(36), primary_key=True), sa.Column("name", sa.String(150), nullable=False, unique=True), sa.Column("total_name", sa.String(150), nullable=False), sa.Column("threshold", sa.Numeric(15,2), nullable=False), sa.Column("severity", sa.String(20), nullable=False), sa.Column("owner", sa.String(255)), sa.Column("enabled", sa.Boolean(), nullable=False), sa.Column("updated_by", sa.String(255)), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
def downgrade(): op.drop_table("configured_audit_rules")
